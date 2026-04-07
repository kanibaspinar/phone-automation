from flask import Blueprint

bp = Blueprint('admin', __name__, template_folder='templates')

from app.admin import routes 