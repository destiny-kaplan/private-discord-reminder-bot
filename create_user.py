"""
Simple script to create additional users after setup has been ran
"""

import sys
from getpass import getpass
import database

def create_new_user():
    """Create a new user"""
    print("Create New User Account")
    print("=" * 30)
    
    # Get username
    while True:
        username = input("Enter username: ").strip()
        if not username:
            print("Username cannot be empty!")
            continue
            
        # Check if username already exists
        existing_user = database.fetch_user(username)
        if existing_user:
            print(f"Username '{username}' already exists! Please choose a different username.")
            continue
        break
    
    # Get password
    while True:
        password = getpass("Enter password: ").strip()
        if len(password) < 6:
            print("Password must be at least 6 characters!")
            continue
        break
    
    # Confirm password
    confirm_password = getpass("Confirm password: ").strip()
    if password != confirm_password:
        print("Passwords don't match!")
        return False
    
    # Create the user
    try:
        database.add_user(username, password)
        print(f"\nUser '{username}' created successfully!")
        return True
    except Exception as e:
        print(f"Failed to create user: {e}")
        return False

def list_existing_users():
    """Show existing users (usernames only for security)"""
    try:
        import sqlite3
        conn = sqlite3.connect(database.DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users")
        users = cursor.fetchall()
        conn.close()
        
        if users:
            print("\nExisting users:")
            for i, (username,) in enumerate(users, 1):
                print(f"  {i}. {username}")
        else:
            print("\nNo users found in database.")
    except Exception as e:
        print(f"Error listing users: {e}")

def main():
    # Initialize database first
    try:
        database.init_db()
        print("Database connection successful.")
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)
    
    while True:
        print("\n" + "="*50)
        print("User Management")
        print("="*50)
        print("1. Create new user")
        print("2. List existing users")
        print("3. Exit")
        
        choice = input("\nEnter your choice (1-3): ").strip()
        
        if choice == "1":
            create_new_user()
        elif choice == "2":
            list_existing_users()
        elif choice == "3":
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")

if __name__ == "__main__":
    main()