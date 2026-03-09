import os
from dotenv import load_dotenv
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()

class Config:
    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
    
    # Database configuration
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///instagram_farm.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Session configuration
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    
    # API configuration
    API_RATE_LIMIT = '500/hour'
    EXTERNAL_API_BASE_URL = os.environ['EXTERNAL_API_BASE_URL']
    
    # Instagram automation configuration
    INSTAGRAM_LOGIN_TIMEOUT = 30  # seconds
    INSTAGRAM_ACTION_DELAY = 2    # seconds between actions
    MAX_DAILY_ACTIONS = {
        'likes': 50,
        'comments': 20,
        'follows': 30,
        'unfollows': 30,
        'stories': 100
    } 