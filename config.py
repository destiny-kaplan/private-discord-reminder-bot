import os
import secrets
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Discord Bot Token
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# Commands Channel ID - Where users can run bot commands
DISCORD_COMMANDS_CHANNEL_ID = int(os.getenv("DISCORD_COMMANDS_CHANNEL_ID", "0"))

# Notifications Channel ID - Where bot posts updates and notifications
DISCORD_NOTIFICATIONS_CHANNEL_ID = int(os.getenv("DISCORD_NOTIFICATIONS_CHANNEL_ID", "0"))

# Database file path
DATABASE = "tasks.db"

# Flask secret key for sessions
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

# Security settings
DEBUG = False  # NEVER set to True in production
HOST = "0.0.0.0"  # Use "127.0.0.1" for localhost-only access
PORT = 5000