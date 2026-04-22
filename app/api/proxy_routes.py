from flask import jsonify, request
from app.extensions import db
from app.models.proxy import Proxy
from app.utils import proxy_manager
import logging

logger = logging.getLogger(__name__)

_DEVICE_TASK_PREFIX = "device_"


def _device_task_id(device_id: str) -> str:
    return f"{_DEVICE_TASK_PREFIX}{device_id}"


def _device_id_from_task(task_id: str) -> str:
    if task_id and task_id.startswith(_DEVICE_TASK_PREFIX):
        return task_id[len(_DEVICE_TASK_PREFIX):]
    return ""


def get_proxy_config():
    """GET /api/proxy/config — return current API key (masked) and balance."""
    key = proxy_manager.get_api_key()
    masked = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("*" * len(key))
    balance = {}
    if key:
        try:
            client = proxy_manager.Proxy6Client(key)
            balance = client.get_balance()
        except Exception as exc:
            balance = {"error": str(exc)}
    return jsonify({"api_key_set": bool(key), "api_key_masked": masked, "balance": balance})


def set_proxy_config():
    """POST /api/proxy/config — save proxy6.net API key."""
    data = request.get_json(force=True) or {}
    key = (data.get("api_key") or "").strip()
    if not key:
        return jsonify({"error": "api_key is required"}), 400
    proxy_manager.set_api_key(key)
    return jsonify({"ok": True})


def sync_proxies():
    """POST /api/proxy/sync — pull proxies from proxy6.net into DB."""
    key = proxy_manager.get_api_key()
    if not key:
        return jsonify({"error": "proxy6.net API key not configured"}), 400
    try:
        added, updated = proxy_manager.sync_proxies(key)
        return jsonify({"ok": True, "added": added, "updated": updated})
    except Exception as exc:
        logger.error("[PROXY] Sync error: %s", exc)
        return jsonify({"error": str(exc)}), 500


def list_proxies():
    """GET /api/proxy/list — return all proxies."""
    proxies = Proxy.query.order_by(Proxy.status, Proxy.country).all()
    return jsonify([p.to_dict() for p in proxies])


def delete_proxy(proxy_id):
    """DELETE /api/proxy/<proxy_id> — remove a proxy from DB."""
    proxy = Proxy.query.filter_by(proxy_id=str(proxy_id)).first()
    if not proxy:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(proxy)
    db.session.commit()
    return jsonify({"ok": True})


def assign_proxy():
    """POST /api/proxy/assign — start Gnirehtet, acquire a proxy, set device proxy."""
    data = request.get_json(force=True) or {}
    device_id = (data.get("device_id") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id is required"}), 400

    task_id = _device_task_id(device_id)

    # Check if already assigned
    existing = Proxy.query.filter_by(assigned_to=task_id, status="in_use").first()
    if existing:
        return jsonify({
            "ok": True,
            "already_assigned": True,
            "proxy_id": existing.proxy_id,
            "host": existing.host,
            "port": existing.port,
        })

    # Start Gnirehtet
    try:
        proxy_manager.start_gnirehtet(device_id)
    except Exception as exc:
        logger.error("[PROXY] Gnirehtet start failed for %s: %s", device_id, exc)
        return jsonify({"error": f"Gnirehtet failed: {exc}"}), 500

    # Acquire proxy from pool
    proxy = proxy_manager.acquire_proxy(task_id)
    if not proxy:
        return jsonify({"error": "No available proxies in pool"}), 503

    # Start local forwarder
    try:
        fwd = proxy_manager.start_forwarder(task_id, proxy.host, proxy.port,
                                            proxy.user, proxy.password)
    except Exception as exc:
        proxy_manager.release_proxy(task_id)
        return jsonify({"error": f"Forwarder start failed: {exc}"}), 500

    # Point device at local forwarder
    try:
        proxy_manager.set_device_proxy(device_id, proxy_manager.GNIREHTET_GATEWAY,
                                       fwd.local_port)
    except Exception as exc:
        logger.error("[PROXY] set_device_proxy failed for %s: %s", device_id, exc)

    logger.info("[PROXY] Assigned proxy %s to device %s via port %d",
                proxy.proxy_id, device_id, fwd.local_port)
    return jsonify({
        "ok": True,
        "proxy_id": proxy.proxy_id,
        "host": proxy.host,
        "port": proxy.port,
        "forwarder_port": fwd.local_port,
    })


def unassign_proxy():
    """POST /api/proxy/unassign — release a proxy and clear the device's system proxy."""
    data = request.get_json(force=True) or {}
    proxy_id = (data.get("proxy_id") or "").strip()
    device_id = (data.get("device_id") or "").strip()

    if proxy_id:
        proxy = Proxy.query.filter_by(proxy_id=proxy_id, status="in_use").first()
    elif device_id:
        task_id = _device_task_id(device_id)
        proxy = Proxy.query.filter_by(assigned_to=task_id, status="in_use").first()
    else:
        return jsonify({"error": "proxy_id or device_id is required"}), 400

    if not proxy:
        return jsonify({"ok": True, "note": "No active assignment found"})

    task_id = proxy.assigned_to or ""
    dev_id = _device_id_from_task(task_id) or device_id

    # Clear device proxy
    if dev_id:
        try:
            proxy_manager.clear_device_proxy(dev_id)
        except Exception as exc:
            logger.warning("[PROXY] clear_device_proxy failed for %s: %s", dev_id, exc)

    # Stop local forwarder
    proxy_manager.stop_forwarder(task_id)

    # Release proxy back to pool
    proxy.status = "available"
    proxy.assigned_to = None
    db.session.commit()

    logger.info("[PROXY] Unassigned proxy %s from device %s", proxy.proxy_id, dev_id)
    return jsonify({"ok": True, "proxy_id": proxy.proxy_id})


def bulk_unassign_proxies():
    """POST /api/proxy/bulk-unassign — release multiple proxies by proxy_id list."""
    data = request.get_json(force=True) or {}
    proxy_ids = data.get("proxy_ids") or []
    if not proxy_ids:
        return jsonify({"error": "proxy_ids list is required"}), 400

    released = 0
    for pid in proxy_ids:
        proxy = Proxy.query.filter_by(proxy_id=str(pid), status="in_use").first()
        if not proxy:
            continue
        task_id = proxy.assigned_to or ""
        dev_id = _device_id_from_task(task_id)
        if dev_id:
            try:
                proxy_manager.clear_device_proxy(dev_id)
            except Exception:
                pass
        proxy_manager.stop_forwarder(task_id)
        proxy.status = "available"
        proxy.assigned_to = None
        released += 1

    db.session.commit()
    logger.info("[PROXY] Bulk unassigned %d proxies", released)
    return jsonify({"ok": True, "released": released})
