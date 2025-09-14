"""
Setup script for the Tasks & Events Management System
Run this script to initialize the database and create your first user
"""

import sys
import os
from getpass import getpass
import database

def setup_database():
    """Initialize the database for the first time"""
    print("🔧 Setting up database...")
    try:
        database.init_db()
        print("✅ Database initialized successfully!")
        return True
    except Exception as e:
        print(f"❌ Database setup failed: {e}")
        return False

def create_user():
    """Create the first user"""
    print("\n👤 Let's create your admin user:")
    
    while True:
        username = input("Enter username: ").strip()
        if username:
            break
        print("Username cannot be empty!")
    
    while True:
        password = getpass("Enter password: ").strip()
        if len(password) >= 6:
            break
        print("Password must be at least 6 characters!")
    
    confirm_password = getpass("Confirm password: ").strip()
    if password != confirm_password:
        print("❌ Passwords don't match!")
        return False
    
    try:
        database.add_user(username, password)
        print(f"✅ User '{username}' created successfully!")
        return True
    except Exception as e:
        print(f"❌ Failed to create user: {e}")
        return False

def check_config():
    """Check if config.py exists and has required settings"""
    if not os.path.exists('config.py'):
        print("❌ config.py not found!")
        print("📝 Please create config.py from the template and add your Discord bot token and channel ID")
        return False
    
    try:
        import config
        if not hasattr(config, 'DISCORD_TOKEN') or config.DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
            print("❌ DISCORD_TOKEN not configured in config.py")
            return False
        
        if not hasattr(config, 'DISCORD_CHANNEL_ID') or isinstance(config.DISCORD_CHANNEL_ID, str):
            print("❌ DISCORD_CHANNEL_ID not configured in config.py")
            return False
        
        print("✅ Configuration looks good!")
        return True
        
    except ImportError as e:
        print(f"❌ Error importing config.py: {e}")
        return False

def main():
    print("🚀 Tasks & Events Management System Setup")
    print("=" * 50)
    
    # Check configuration
    if not check_config():
        print("\n📋 Setup Steps:")
        print("1. Copy the config.py template")
        print("2. Get your Discord bot token from https://discord.com/developers/applications")
        print("3. Get your Discord channel ID (right-click channel > Copy ID)")
        print("4. Update config.py with your values")
        print("5. Run this setup script again")
        sys.exit(1)
    
    # Setup database
    if not setup_database():
        sys.exit(1)
    
    # Create user
    if not create_user():
        sys.exit(1)
    
    print("\n🎉 Setup completed successfully!")
    print("\n📋 Next steps:")
    print("1. Run: python main.py")
    print("2. Open your browser to http://localhost:5000")
    print("3. Login with the credentials you just created")
    print("4. Your Discord bot should be online and ready!")
    
    print("\n📱 Discord Commands Available:")
    print("!addevent <n> | <due_date> | [mention] | [category] | [notes] | [priority]")
    print("!addtask <n> | <due_date> | [mention] | [category] | [notes] | [priority]")
    print("!eventlist - Show incomplete events")
    print("!tasklist - Show incomplete tasks")
    print("!searchevent <keyword> - Search events")
    print("!searchtask <keyword> - Search tasks")
    print("!completeevent <id> - Mark event complete")
    print("!completetask <id> - Mark task complete")

if __name__ == "__main__":
    main()