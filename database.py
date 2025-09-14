import sqlite3
import os
import stat
from werkzeug.security import generate_password_hash

DATABASE = "tasks.db"

def init_db():
    """Initialize database with secure permissions"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Tasks table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK(type IN ('task', 'event')),
        name TEXT NOT NULL,
        due_date TEXT NOT NULL,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
        mention TEXT DEFAULT '',
        repeat_interval TEXT DEFAULT 'none' CHECK(repeat_interval IN ('none', 'daily', 'weekly', 'monthly')),
        category TEXT DEFAULT 'Misc',
        notes TEXT DEFAULT '',
        priority TEXT DEFAULT 'Medium' CHECK(priority IN ('Low', 'Medium', 'High')),
        color TEXT DEFAULT '#3399ff',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Create indexes for better performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date)")

    conn.commit()
    conn.close()
    
    # Set restrictive file permissions (owner read/write only)
    if os.path.exists(DATABASE):
        try:
            os.chmod(DATABASE, stat.S_IRUSR | stat.S_IWUSR)
        except Exception as e:
            print(f"Warning: Could not set database file permissions: {e}")

def check_column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        return column_name in columns
    except Exception:
        return False

def add_missing_columns():
    """Add missing columns to existing database if needed"""
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        # Check and add updated_at column if it doesn't exist
        if not check_column_exists('tasks', 'updated_at'):
            cursor.execute("ALTER TABLE tasks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            print("Added missing updated_at column")
        
        # Check and add created_at column if it doesn't exist
        if not check_column_exists('tasks', 'created_at'):
            cursor.execute("ALTER TABLE tasks ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            print("Added missing created_at column")
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: Could not add missing columns: {e}")

def add_user(username, password):
    """Add a new user with input validation"""
    if not username or not password:
        raise ValueError("Username and password are required")
    
    if len(username) > 50:
        raise ValueError("Username too long")
    
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    try:
        password_hash = generate_password_hash(password)
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("Username already exists")
    except Exception as e:
        conn.close()
        raise e
    
    conn.close()

def fetch_user(username):
    """Fetch user by username with input validation"""
    if not username or len(username) > 50:
        return None
        
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash FROM users WHERE username=?", (username,))
    user = cursor.fetchone()
    conn.close()
    return user

def fetch_items():
    """Fetch all items with proper error handling"""
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY due_date ASC")
        items = cursor.fetchall()
        conn.close()

        safe_items = []
        for item in items:
            if len(item) >= 11:  # Ensures all expected columns are present
                safe_items.append({
                    "id": item[0] or 0,
                    "type": item[1] or "task",
                    "name": item[2] or "",
                    "due_date": item[3] or "",
                    "status": item[4] or "pending",
                    "mention": item[5] or "",
                    "repeat_interval": item[6] or "none",
                    "category": item[7] or "Misc",
                    "notes": item[8] or "",
                    "priority": item[9] or "Medium",
                    "color": item[10] or "#3399ff"
                })
        return safe_items
    except Exception as e:
        print(f"Error fetching items: {e}")
        return []

def add_item_db(item_type, name, due_date, mention="", repeat_interval="none",
                category="Misc", notes="", priority="Medium", color=None):
    """Add item with comprehensive input validation"""
    # Input validation
    if not item_type or item_type not in ["task", "event"]:
        raise ValueError("Invalid item type")
    
    if not name or len(name) > 100:
        raise ValueError("Invalid name")
    
    if not due_date:
        raise ValueError("Due date is required")
    
    if priority not in ["Low", "Medium", "High"]:
        priority = "Medium"
    
    if repeat_interval not in ["none", "daily", "weekly", "monthly"]:
        repeat_interval = "none"
    
    # Truncate fields to prevent database errors
    mention = (mention or "")[:50]
    category = (category or "Misc")[:30]
    notes = (notes or "")[:500]
    color = (color or "#3399ff")[:20]
    
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (type, name, due_date, mention, repeat_interval, category, notes, priority, color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_type, name, due_date, mention, repeat_interval, category, notes, priority, color))
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return new_id
    except Exception as e:
        print(f"Error adding item: {e}")
        raise e

def update_item_status(item_id, status):
    """Update item status with validation and fallback for missing columns"""
    if not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("Invalid item ID")
    
    if status not in ["pending", "completed"]:
        raise ValueError("Invalid status")
    
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        # Try with updated_at column first
        try:
            cursor.execute("UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, item_id))
        except sqlite3.OperationalError as e:
            if "no such column: updated_at" in str(e):
                # Fallback: update without updated_at column
                print("Warning: updated_at column not found, updating without timestamp")
                cursor.execute("UPDATE tasks SET status=? WHERE id=?", (status, item_id))
            else:
                raise e
                
        conn.commit()
        affected_rows = cursor.rowcount
        conn.close()
        
        if affected_rows == 0:
            raise ValueError(f"No item found with ID {item_id}")
            
    except Exception as e:
        print(f"Error updating item status: {e}")
        raise e

def get_item(item_id):
    """Get single item with validation"""
    if not isinstance(item_id, int) or item_id <= 0:
        return None
    
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id=?", (item_id,))
        item = cursor.fetchone()
        conn.close()
        return item
    except Exception as e:
        print(f"Error getting item: {e}")
        return None

def update_item_db(item_id, **fields):
    """Update a task by item_id with enhanced security. Fields is a dict of column=value pairs."""
    if not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("Invalid item ID")
    
    if not fields:
        return
    
    # Whitelist allowed columns with validation
    allowed_fields = {
        "type": lambda x: x if x in ["task", "event"] else None,
        "name": lambda x: x[:100] if x and len(x) <= 100 else None,
        "due_date": lambda x: x[:50] if x else None,
        "status": lambda x: x if x in ["pending", "completed"] else None,
        "mention": lambda x: (x or "")[:50],
        "repeat_interval": lambda x: x if x in ["none", "daily", "weekly", "monthly"] else None,
        "category": lambda x: (x or "Misc")[:30],
        "notes": lambda x: (x or "")[:500],
        "priority": lambda x: x if x in ["Low", "Medium", "High"] else None,
        "color": lambda x: (x or "#3399ff")[:20]
    }
    
    # Filter and validate fields
    safe_fields = {}
    for k, v in fields.items():
        if k in allowed_fields:
            validated_value = allowed_fields[k](v)
            if validated_value is not None:
                safe_fields[k] = validated_value
    
    if not safe_fields:
        return
        
    # Build query with validated fields
    set_clauses = [f"{k}=?" for k in safe_fields.keys()]
    params = list(safe_fields.values()) + [item_id]
    
    # Try to add updated timestamp if column exists
    has_updated_at = check_column_exists('tasks', 'updated_at')
    if has_updated_at:
        set_clauses.append("updated_at=CURRENT_TIMESTAMP")
    
    sql = f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id=?"
    
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error updating item: {e}")
        raise e

def delete_item(item_id):
    """Delete an item with validation"""
    if not isinstance(item_id, int) or item_id <= 0:
        raise ValueError("Invalid item ID")
    
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id=?", (item_id,))
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()
        return rows_affected > 0
    except Exception as e:
        print(f"Error deleting item: {e}")
        raise e

def get_user_count():
    """Get total number of users"""
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        print(f"Error getting user count: {e}")
        return 0

def cleanup_old_completed_tasks(days_old=90):
    """Clean up completed tasks older than specified days"""
    try:
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        # Check if updated_at column exists before using it
        has_updated_at = check_column_exists('tasks', 'updated_at')
        
        if has_updated_at:
            cursor.execute("""
                DELETE FROM tasks 
                WHERE status = 'completed' 
                AND datetime(updated_at) < datetime('now', '-{} days')
            """.format(days_old))
        else:
            # Fallback to created_at if updated_at doesn't exist
            cursor.execute("""
                DELETE FROM tasks 
                WHERE status = 'completed' 
                AND datetime(created_at) < datetime('now', '-{} days')
            """.format(days_old))
            
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted_count
    except Exception as e:
        print(f"Error cleaning up old tasks: {e}")
        return 0

def migrate_database():
    """Run database migrations to ensure all columns exist"""
    print("Checking database schema...")
    add_missing_columns()
    print("Database migration complete.")

# Initialize database and run migrations on import
if __name__ == "__main__" or DATABASE:
    try:
        init_db()
        migrate_database()
    except Exception as e:
        print(f"Warning: Database initialization error: {e}")