import os
from flask import Flask
from config import Config
from app.extensions import db, migrate
from app.utils.auto_device_manager import AutoDeviceManager
from app.utils.device_manager import init_device_manager
from app.utils.background_tasks import init_background_tasks
from app.template_filters import bp as filters_bp
from flask_cors import CORS
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def recreate_db(app):
    """Recreate all database tables"""
    with app.app_context():
        # Create all tables
        db.create_all()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Get the application root directory
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Configure the assets directory
    assets_dir = os.path.join(app_root, 'assets')
    if not os.path.exists(assets_dir):
        os.makedirs(assets_dir)
        logger.info(f"Created assets directory at: {assets_dir}")
    
    # Configure database
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app_root, 'instagram_farm.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app)

    # Register blueprints
    from app.api import bp as api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Register template filters
    app.register_blueprint(filters_bp)

    # Recreate database tables
    recreate_db(app)

    with app.app_context():
        # Initialize device manager first
        device_manager = init_device_manager(assets_dir)
        if not device_manager:
            logger.error("Failed to initialize global device manager")
            return None

        # Initialize auto device manager
        auto_device_manager = AutoDeviceManager()
        auto_device_manager.init_app(app)

        # Initialize background tasks last
        init_background_tasks(app)

    return app 