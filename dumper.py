import uiautomator2 as u2
import xml.etree.ElementTree as ET
import os
import time
import requests

# Replace with your device ID
device_id = "PLEGAR1791507808"

def log(message):
    print(f"[LOG] {message}")

def download_and_install_uiautomator_apk(device_id):
    apk_url = "https://github.com/openatx/android-uiautomator-server/releases/latest/download/app-uiautomator.apk"
    apk_path = "app-uiautomator.apk"

    log("Downloading the latest uiautomator2 APK...")
    response = requests.get(apk_url)
    with open(apk_path, 'wb') as apk_file:
        apk_file.write(response.content)
    log("Downloaded the uiautomator2 APK.")

    log("Installing the uiautomator2 APK on the device...")
    os.system(f"adb -s {device_id} install -r {apk_path}")
    log("Installed the uiautomator2 APK on the device.")

    log("Granting necessary permissions...")
    os.system(f"adb -s {device_id} shell pm grant com.github.uiautomator android.permission.SYSTEM_ALERT_WINDOW")
    os.system(f"adb -s {device_id} shell pm grant com.github.uiautomator android.permission.WRITE_EXTERNAL_STORAGE")
    os.system(f"adb -s {device_id} shell pm grant com.github.uiautomator android.permission.READ_EXTERNAL_STORAGE")
    os.system(f"adb -s {device_id} shell pm grant com.github.uiautomator android.permission.REQUEST_INSTALL_PACKAGES")
    os.system(f"adb -s {device_id} shell pm grant com.github.uiautomator android.permission.INTERNET")
    log("Granted necessary permissions.")

def connect_to_device(device_id):
    log("Connecting to the device...")
    d = u2.connect(device_id)
    log("Connected to the device.")
    return d

def launch_app(d, package_name, activity_name):
    log(f"Launching the application {package_name}...")
    d.app_start(package_name, activity_name)
    time.sleep(5)
    log(f"Application {package_name} launched.")

def dump_ui_hierarchy(d):
    log("Dumping the current screen UI hierarchy...")
    ui_hierarchy = d.dump_hierarchy()
    log("UI hierarchy dumped.")
    return ui_hierarchy

def parse_ui_hierarchy(xml_data):
    log("Parsing UI hierarchy...")
    root = ET.fromstring(xml_data)
    elements = []
    for elem in root.iter():
        element_info = {
            'text': elem.attrib.get('text', ''),
            'description': elem.attrib.get('content-desc', ''),
            'resource-id': elem.attrib.get('resource-id', ''),
            'class': elem.attrib.get('class', '')
        }
        elements.append(element_info)
    return elements

def print_ui_elements(elements):
    for elem in elements:
        print(f"Text: {elem['text']}, Description: {elem['description']}, Resource ID: {elem['resource-id']}, Class: {elem['class']}")

def save_ui_elements_to_file(elements, file_path):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file:
        for elem in elements:
            file.write(f"Text: {elem['text']}, Description: {elem['description']}, Resource ID: {elem['resource-id']}, Class: {elem['class']}\n")
    log(f"UI elements saved to {file_path}.")

def main():
    #download_and_install_uiautomator_apk(device_id)
    d = connect_to_device(device_id)
    #package_name = "com.zhiliaoapp.musically"
    #activity_name = "com.ss.android.ugc.aweme.main.MainActivity"
    #launch_app(d, package_name, activity_name)
    ui_hierarchy = dump_ui_hierarchy(d)
    elements = parse_ui_hierarchy(ui_hierarchy)
    print_ui_elements(elements)
    file_path = "C:\\Users\\kanib\\Desktop\\tiktokbeta\\tiktok_ui_elements.txt"
    save_ui_elements_to_file(elements, file_path)
    log("Task completed.")

if __name__ == "__main__":
    main()
