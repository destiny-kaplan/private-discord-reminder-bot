import discord
from discord.ext import commands
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, abort
from threading import Thread
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from werkzeug.security import check_password_hash
import sqlite3
import sys
import os
import time
import stat
from functools import wraps
from collections import defaultdict
from dotenv import load_dotenv
import concurrent.futures

# Load environment variables
load_dotenv()

try:
    from config import DISCORD_TOKEN, DISCORD_COMMANDS_CHANNEL_ID, DISCORD_NOTIFICATIONS_CHANNEL_ID, DATABASE, SECRET_KEY, DEBUG, HOST, PORT
except ImportError:
    print("⚠️ Error: config.py not found or missing required variables!")
    print("📝 Please create config.py with the following variables:")
    print("DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', '')")
    print("DISCORD_COMMANDS_CHANNEL_ID = int(os.getenv('DISCORD_COMMANDS_CHANNEL_ID', '0'))")
    print("DISCORD_NOTIFICATIONS_CHANNEL_ID = int(os.getenv('DISCORD_NOTIFICATIONS_CHANNEL_ID', '0'))")
    print("DATABASE = 'tasks.db'")
    print("SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-in-production')")
    print("DEBUG = False")
    print("HOST = '127.0.0.1'")
    print("PORT = 5000")
    sys.exit(1)

import database

# Global handle to the asyncio loop that runs the bot
ASYNC_LOOP = None
BOT_READY = False

# Global thread pool executor for scheduler updates
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="scheduler_thread")

# Rate limiting and security storage
login_attempts = {}
request_counts = defaultdict(list)

def validate_input(data, max_length=200):
    """Basic input validation"""
    if not data:
        return ""
    if isinstance(data, str) and len(data) > max_length:
        return data[:max_length]
    return data

def format_mention_for_discord(mention):
    """Format mention for Discord - ensure @ symbol is present"""
    if not mention:
        return ""
    mention = mention.strip()
    if mention and not mention.startswith('@'):
        mention = '@' + mention
    return mention

# ========================
# Discord Bot Setup
# ========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    global BOT_READY
    BOT_READY = True
    print(f"✅ Discord Bot logged in as {bot.user}") # Confirmation in console that bot has logged in

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"⚠️ Discord bot error in {event}: {args}") # For debugging

def commands_channel_only():
    """Decorator to restrict commands to the designated commands channel"""
    def decorator(func):
        @wraps(func)
        async def command_wrapper(ctx, *args, **kwargs):
            if ctx.channel.id != DISCORD_COMMANDS_CHANNEL_ID:
                await ctx.send(f"⚠️ Commands can only be used in <#{DISCORD_COMMANDS_CHANNEL_ID}>")
                return
            return await func(ctx, *args, **kwargs)
        return command_wrapper
    return decorator

def format_time_12hour(datetime_str):
    """Format datetime string to 12-hour format with AM/PM"""
    try:
        dt = datetime.fromisoformat(datetime_str)
        return dt.strftime("%Y-%m-%d %I:%M %p")
    except:
        return datetime_str

@bot.command()
@commands_channel_only()
async def ping(ctx):
    await ctx.send("Pong!")

@bot.command()
@commands_channel_only()
async def addevent(ctx, *, args=None):
    """Add a new event. Usage: !addevent <name> | <due_date> | [mention] | [category] | [notes] | [priority]"""
    if not args:
        await ctx.send("Usage: `!addevent <name> | <due_date> | [mention] | [category] | [notes] | [priority]`\nExample: `!addevent Meeting | 2025-09-07 14:00 | @user | Work | Important meeting | High`")
        return
    
    parts = [p.strip() for p in args.split('|')]
    if len(parts) < 2:
        await ctx.send("Please provide at least name and due date separated by |")
        return
    
    # Input validation
    name = validate_input(parts[0], 100)
    due_date_str = validate_input(parts[1], 50)
    mention = validate_input(parts[2] if len(parts) > 2 else "", 50)
    category = validate_input(parts[3] if len(parts) > 3 else "Misc", 30)
    notes = validate_input(parts[4] if len(parts) > 4 else "", 500)
    priority = parts[5] if len(parts) > 5 and parts[5] in ["Low", "Medium", "High"] else "Medium"
    
    if not name:
        await ctx.send("Event name cannot be empty")
        return
    
    try:
        # Try to parse the date - support both with and without AM/PM
        if any(x in due_date_str.upper() for x in ['AM', 'PM']):
            # Parse 12-hour format
            if len(due_date_str.split()) == 3:  # Date + Time + AM/PM
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d %I:%M %p")
            else:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d %I %p")
        elif len(due_date_str.split()) == 1:  # Only date provided
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
        else:  # 24-hour format
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M")
            
        due_date_iso = due_date.isoformat()
    except ValueError:
        await ctx.send("Invalid date format. Use:\n- YYYY-MM-DD HH:MM (24-hour)\n- YYYY-MM-DD H:MM AM/PM (12-hour)\n- YYYY-MM-DD (date only)")
        return
    
    new_id = database.add_item_db("event", name, due_date_iso, mention, "none", category, notes, priority, "#3399ff")
    
    # Reschedule reminders after adding new item
    if ASYNC_LOOP:
        executor.submit(schedule_reminders_and_updates, ASYNC_LOOP)
    
    embed = discord.Embed(title="✅ Event Added", color=0x3399ff)
    embed.add_field(name="Name", value=name, inline=True)
    embed.add_field(name="Due Date", value=format_time_12hour(due_date_iso), inline=True)
    embed.add_field(name="Priority", value=priority, inline=True)
    if category != "Misc":
        embed.add_field(name="Category", value=category, inline=True)
    if mention:
        embed.add_field(name="Mention", value=mention, inline=True)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    embed.add_field(name="Event ID", value=str(new_id), inline=True)
    
    await ctx.send(embed=embed)

    message = (
        f"🆕 New Event Created!\n\n"
        f"**{name}**\n"
        f"📅 Due: {format_time_12hour(due_date_iso)}\n"
        f"🎯 Priority: {priority}\n"
        f"📂 Category: {category}\n"
        f"📝 Notes: {notes or 'None'}\n"
        f"🆔 ID: {new_id}"
    )
    await notify_discord(message, mention_everyone=True)

@bot.command()
@commands_channel_only()
async def addtask(ctx, *, args=None):
    """Add a new task. Usage: !addtask <name> | <due_date> | [mention] | [category] | [notes] | [priority]"""
    if not args:
        await ctx.send("Usage: `!addtask <name> | <due_date> | [mention] | [category] | [notes] | [priority]`\nExample: `!addtask Homework | 2025-09-07 11:59 PM | @user | School | Chapter 5 | High`")
        return
    
    parts = [p.strip() for p in args.split('|')]
    if len(parts) < 2:
        await ctx.send("Please provide at least name and due date separated by |")
        return
    
    # Input validation
    name = validate_input(parts[0], 100)
    due_date_str = validate_input(parts[1], 50)
    mention = validate_input(parts[2] if len(parts) > 2 else "", 50)
    category = validate_input(parts[3] if len(parts) > 3 else "Misc", 30)
    notes = validate_input(parts[4] if len(parts) > 4 else "", 500)
    priority = parts[5] if len(parts) > 5 and parts[5] in ["Low", "Medium", "High"] else "Medium"
    
    if not name:
        await ctx.send("Task name cannot be empty")
        return
    
    try:
        # Try to parse the date - support both with and without AM/PM
        if any(x in due_date_str.upper() for x in ['AM', 'PM']):
            # Parse 12-hour format
            if len(due_date_str.split()) == 3:  # Date + Time + AM/PM
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d %I:%M %p")
            else:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d %I %p")
        elif len(due_date_str.split()) == 1:  # Only date provided
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
        else:  # 24-hour format
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M")
            
        due_date_iso = due_date.isoformat()
    except ValueError:
        await ctx.send("Invalid date format. Use:\n- YYYY-MM-DD HH:MM (24-hour)\n- YYYY-MM-DD H:MM AM/PM (12-hour)\n- YYYY-MM-DD (date only)")
        return
    
    new_id = database.add_item_db("task", name, due_date_iso, mention, "none", category, notes, priority, "#28a745")
    
    # Reschedule reminders after adding new item
    if ASYNC_LOOP:
        executor.submit(schedule_reminders_and_updates, ASYNC_LOOP)
    
    embed = discord.Embed(title="✅ Task Added", color=0x00ff00)
    embed.add_field(name="Name", value=name, inline=True)
    embed.add_field(name="Due Date", value=format_time_12hour(due_date_iso), inline=True)
    embed.add_field(name="Priority", value=priority, inline=True)
    if category != "Misc":
        embed.add_field(name="Category", value=category, inline=True)
    if mention:
        embed.add_field(name="Mention", value=mention, inline=True)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    embed.add_field(name="Task ID", value=str(new_id), inline=True)
    
    await ctx.send(embed=embed)

    message = (
        f"🆕 New Task Created!\n\n"
        f"**{name}**\n"
        f"📅 Due: {format_time_12hour(due_date_iso)}\n"
        f"🎯 Priority: {priority}\n"
        f"📂 Category: {category}\n"
        f"📝 Notes: {notes or 'None'}\n"
        f"🆔 ID: {new_id}"
    )
    await notify_discord(message, mention_everyone=True)

@bot.command()
@commands_channel_only()
async def searchevent(ctx, *, search_term=None):
    """Search for events by name, category, or notes (all statuses)"""
    if not search_term:
        await ctx.send("Usage: `!searchevent <search_term>`\nExample: `!searchevent meeting`")
        return
    
    try:
        items = database.fetch_items()
        events = [item for item in items if item['type'] == 'event']  # All statuses
        
        # Filter events that match the search term (case-insensitive, partial match)
        matching_events = []
        search_lower = search_term.lower()
        
        for event in events:
            if (search_lower in event['name'].lower() or 
                search_lower in event['category'].lower() or 
                (event['notes'] and search_lower in event['notes'].lower())):
                matching_events.append(event)
        
        if not matching_events:
            await ctx.send(f"No events found matching '{search_term}'")
            return
        
        embed = discord.Embed(title=f"📅 Events matching '{search_term}'", color=0x3399ff)
        
        for event in matching_events[:10]:  # Limit to 10 results
            repeat_text = f" (Repeats {event['repeat_interval']})" if event['repeat_interval'] != 'none' else ""
            status_emoji = "✅" if event['status'] == 'completed' else "⏳"
            status_text = f" [{event['status'].title()}]"
            
            embed.add_field(
                name=f"{status_emoji} {event['name']} (ID: {event['id']}){repeat_text}{status_text}",
                value=f"Due: {format_time_12hour(event['due_date'])}\nPriority: {event['priority']}\nCategory: {event['category']}\nNotes: {event['notes'] or 'None'}",
                inline=False
            )
        
        if len(matching_events) > 10:
            embed.set_footer(text=f"Showing first 10 of {len(matching_events)} matching events")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"ERROR in searchevent: {e}")
        await ctx.send(f"Error searching events: {str(e)}")

@bot.command()
@commands_channel_only()
async def searchtask(ctx, *, search_term=None):
    """Search for tasks by name, category, or notes (all statuses)"""
    if not search_term:
        await ctx.send("Usage: `!searchtask <search_term>`\nExample: `!searchtask homework`")
        return
    
    try:
        items = database.fetch_items()
        tasks = [item for item in items if item['type'] == 'task']  # All statuses
        
        # Filter tasks that match the search term (case-insensitive, partial match)
        matching_tasks = []
        search_lower = search_term.lower()
        
        for task in tasks:
            if (search_lower in task['name'].lower() or 
                search_lower in task['category'].lower() or 
                (task['notes'] and search_lower in task['notes'].lower())):
                matching_tasks.append(task)
        
        if not matching_tasks:
            await ctx.send(f"No tasks found matching '{search_term}'")
            return
        
        embed = discord.Embed(title=f"📋 Tasks matching '{search_term}'", color=0x00ff00)
        
        for task in matching_tasks[:10]:  # Limit to 10 results
            repeat_text = f" (Repeats {task['repeat_interval']})" if task['repeat_interval'] != 'none' else ""
            status_emoji = "✅" if task['status'] == 'completed' else "⏳"
            status_text = f" [{task['status'].title()}]"
            
            embed.add_field(
                name=f"{status_emoji} {task['name']} (ID: {task['id']}){repeat_text}{status_text}",
                value=f"Due: {format_time_12hour(task['due_date'])}\nPriority: {task['priority']}\nCategory: {task['category']}\nNotes: {task['notes'] or 'None'}",
                inline=False
            )
        
        if len(matching_tasks) > 10:
            embed.set_footer(text=f"Showing first 10 of {len(matching_tasks)} matching tasks")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"ERROR in searchtask: {e}")
        await ctx.send(f"Error searching tasks: {str(e)}")

# DEBUG COMMANDS - For troubleshooting
@bot.command()
@commands_channel_only()
async def debug(ctx):
    """Debug command to test bot functionality"""
    try:
        items = database.fetch_items()
        total_items = len(items)
        pending_tasks = len([item for item in items if item['type'] == 'task' and item['status'] == 'pending'])
        pending_events = len([item for item in items if item['type'] == 'event' and item['status'] == 'pending'])
        completed_items = len([item for item in items if item['status'] == 'completed'])
        
        debug_info = f"""**Debug Information:**
Total items in database: {total_items}
Pending tasks: {pending_tasks}
Pending events: {pending_events}
Completed items: {completed_items}
Bot ready: {BOT_READY}
Channel ID: {ctx.channel.id}
Expected commands channel: {DISCORD_COMMANDS_CHANNEL_ID}"""
        
        await ctx.send(debug_info)
        
    except Exception as e:
        await ctx.send(f"Debug error: {str(e)}")
        print(f"Debug command error: {e}")

@bot.command()
@commands_channel_only()
async def completetask(ctx, task_id=None):
    """Complete a task by ID"""
    if not task_id:
        await ctx.send("Usage: `!completetask <task_id>`\nExample: `!completetask 123`")
        return
    
    try:
        task_id = int(task_id)
    except ValueError:
        await ctx.send("Invalid task ID. Please provide a number.")
        return
    
    item = database.get_item(task_id)
    if not item:
        await ctx.send(f"Task with ID {task_id} not found.")
        return
    
    if item[1] != "task":
        await ctx.send(f"ID {task_id} is not a task. Use `!completeevent` for events.")
        return
    
    if item[4] == "completed":
        await ctx.send(f"Task '{item[2]}' is already completed.")
        return
    
    # Update status in database
    database.update_item_status(task_id, "completed")
    
    # Send completion notification
    mention = format_mention_for_discord(item[5]) if item[5] else ""
    
    embed = discord.Embed(title="✅ Task Completed!", color=0x00ff00)
    embed.add_field(name="Task", value=item[2], inline=True)
    embed.add_field(name="Priority", value=item[9], inline=True)
    embed.add_field(name="Category", value=item[7], inline=True)
    if mention:
        embed.add_field(name="Mention", value=mention, inline=True)
    if item[8]:
        embed.add_field(name="Notes", value=item[8], inline=False)
    
    await ctx.send(embed=embed)

    mention = format_mention_for_discord(item[5]) if item[5] else ""
    message = (
        f"✅ Task Completed!\n\n"
        f"**{item[2]}**{' ' + mention if mention else ''}\n"
        f"🎯 Priority: {item[9]}\n"
        f"📝 Notes: {item[8] or 'None'}"
    )
    await notify_discord(message, mention_everyone=True)

@bot.command()
@commands_channel_only()
async def completeevent(ctx, event_id=None):
    """Complete an event by ID"""
    if not event_id:
        await ctx.send("Usage: `!completeevent <event_id>`\nExample: `!completeevent 123`")
        return
    
    try:
        event_id = int(event_id)
    except ValueError:
        await ctx.send("Invalid event ID. Please provide a number.")
        return
    
    item = database.get_item(event_id)
    if not item:
        await ctx.send(f"Event with ID {event_id} not found.")
        return
    
    if item[1] != "event":
        await ctx.send(f"ID {event_id} is not an event. Use `!completetask` for tasks.")
        return
    
    if item[4] == "completed":
        await ctx.send(f"Event '{item[2]}' is already completed.")
        return
    
    # Update status in database
    database.update_item_status(event_id, "completed")
    
    # Send completion notification
    mention = format_mention_for_discord(item[5]) if item[5] else ""
    
    embed = discord.Embed(title="✅ Event Completed!", color=0x3399ff)
    embed.add_field(name="Event", value=item[2], inline=True)
    embed.add_field(name="Priority", value=item[9], inline=True)
    embed.add_field(name="Category", value=item[7], inline=True)
    if mention:
        embed.add_field(name="Mention", value=mention, inline=True)
    if item[8]:
        embed.add_field(name="Notes", value=item[8], inline=False)
    
    await ctx.send(embed=embed)

    mention = format_mention_for_discord(item[5]) if item[5] else ""
    message = (
        f"✅ Event Completed!\n\n"
        f"**{item[2]}**{' ' + mention if mention else ''}\n"
        f"🎯 Priority: {item[9]}\n"
        f"📝 Notes: {item[8] or 'None'}"
    )
    await notify_discord(message, mention_everyone=True)

@bot.command()
@commands_channel_only()
async def eventlist(ctx):
    """Show all incomplete events"""
    items = database.fetch_items()
    events = [item for item in items if item['type'] == 'event' and item['status'] == 'pending']
    
    if not events:
        await ctx.send("No incomplete events found!")
        return
    
    embed = discord.Embed(title="📅 Incomplete Events", color=0x3399ff)
    for event in events[:15]:  # Limit to 15 results
        repeat_text = f" (Repeats {event['repeat_interval']})" if event['repeat_interval'] != 'none' else ""
        embed.add_field(
            name=f"{event['name']} (ID: {event['id']}){repeat_text}",
            value=f"Due: {format_time_12hour(event['due_date'])}\nPriority: {event['priority']}\nCategory: {event['category']}",
            inline=False
        )
    
    if len(events) > 15:
        embed.set_footer(text=f"Showing first 15 of {len(events)} incomplete events")
    
    await ctx.send(embed=embed)

@bot.command()
@commands_channel_only()
async def tasklist(ctx):
    """Show all incomplete tasks"""
    items = database.fetch_items()
    tasks = [item for item in items if item['type'] == 'task' and item['status'] == 'pending']
    
    if not tasks:
        await ctx.send("No incomplete tasks found!")
        return
    
    embed = discord.Embed(title="📋 Incomplete Tasks", color=0x00ff00)
    for task in tasks[:15]:  # Limit to 15 results
        repeat_text = f" (Repeats {task['repeat_interval']})" if task['repeat_interval'] != 'none' else ""
        embed.add_field(
            name=f"{task['name']} (ID: {task['id']}){repeat_text}",
            value=f"Due: {format_time_12hour(task['due_date'])}\nPriority: {task['priority']}\nCategory: {task['category']}",
            inline=False
        )
    
    if len(tasks) > 15:
        embed.set_footer(text=f"Showing first 15 of {len(tasks)} incomplete tasks")
    
    await ctx.send(embed=embed)

async def notify_discord(message: str, mention_everyone: bool = False):
    """Send a message to the configured DISCORD_NOTIFICATIONS_CHANNEL_ID with optional @everyone mention."""
    if not BOT_READY:
        print(f"⚠️ Discord bot not ready, message not sent: {message}")
        return
        
    channel = bot.get_channel(DISCORD_NOTIFICATIONS_CHANNEL_ID)
    if channel:
        try:
            # Add @everyone mention if requested
            full_message = f"@everyone\n{message}" if mention_everyone else message
            await channel.send(full_message)
            print(f"✅ Discord message sent: {message[:50]}...")
        except Exception as e:
            print("⚠️ Error sending discord message:", e)
    else:
        print("⚠️ Discord notifications channel not found:", DISCORD_NOTIFICATIONS_CHANNEL_ID)

async def send_daily_update():
    """Send daily update with all pending tasks and events to notifications channel"""
    if not BOT_READY:
        print("⚠️ Discord bot not ready for daily update")
        return
    
    print("📅 Sending daily update...")
    
    try:
        items = database.fetch_items()
        pending_tasks = [item for item in items if item['type'] == 'task' and item['status'] == 'pending']
        pending_events = [item for item in items if item['type'] == 'event' and item['status'] == 'pending']
        
        # Create daily summary
        today = datetime.now()
        
        # Filter items due today or overdue
        overdue_tasks = []
        today_tasks = []
        upcoming_tasks = []
        
        overdue_events = []
        today_events = []
        upcoming_events = []
        
        for task in pending_tasks:
            try:
                due_date = datetime.fromisoformat(task['due_date'])
                if due_date.date() < today.date():
                    overdue_tasks.append(task)
                elif due_date.date() == today.date():
                    today_tasks.append(task)
                elif (due_date.date() - today.date()).days <= 7:
                    upcoming_tasks.append(task)
            except:
                upcoming_tasks.append(task)  # If date parsing fails, add to upcoming
        
        for event in pending_events:
            try:
                due_date = datetime.fromisoformat(event['due_date'])
                if due_date.date() < today.date():
                    overdue_events.append(event)
                elif due_date.date() == today.date():
                    today_events.append(event)
                elif (due_date.date() - today.date()).days <= 7:
                    upcoming_events.append(event)
            except:
                upcoming_events.append(event)
        
        # Build embed message
        embed = discord.Embed(
            title="📋 Daily Update - Tasks & Events",
            description=f"Daily summary for {today.strftime('%A, %B %d, %Y')}",
            color=0x3399ff
        )
        
        # Overdue items (high priority)
        if overdue_tasks or overdue_events:
            overdue_text = ""
            for task in overdue_tasks[:5]:
                overdue_text += f"🔴 **{task['name']}** (ID: {task['id']}) - Due: {format_time_12hour(task['due_date'])} - Priority: {task['priority']}\n"
            for event in overdue_events[:5]:
                overdue_text += f"🔴 **{event['name']}** (ID: {event['id']}) - Due: {format_time_12hour(event['due_date'])} - Priority: {event['priority']}\n"
            
            if overdue_text:
                embed.add_field(name="⚠️ OVERDUE", value=overdue_text[:1024], inline=False)
        
        # Today's items
        if today_tasks or today_events:
            today_text = ""
            for task in today_tasks[:5]:
                today_text += f"🟡 **{task['name']}** (ID: {task['id']}) - Priority: {task['priority']}\n"
            for event in today_events[:5]:
                today_text += f"🟡 **{event['name']}** (ID: {event['id']}) - Priority: {event['priority']}\n"
            
            if today_text:
                embed.add_field(name="📅 DUE TODAY", value=today_text[:1024], inline=False)
        
        # Upcoming items (next 7 days)
        if upcoming_tasks or upcoming_events:
            upcoming_text = ""
            for task in upcoming_tasks[:5]:
                upcoming_text += f"🟢 **{task['name']}** (ID: {task['id']}) - Due: {format_time_12hour(task['due_date'])} - Priority: {task['priority']}\n"
            for event in upcoming_events[:5]:
                upcoming_text += f"🟢 **{event['name']}** (ID: {event['id']}) - Due: {format_time_12hour(event['due_date'])} - Priority: {event['priority']}\n"
            
            if upcoming_text:
                embed.add_field(name="📈 UPCOMING (Next 7 Days)", value=upcoming_text[:1024], inline=False)
        
        # Summary statistics
        total_pending = len(pending_tasks) + len(pending_events)
        total_overdue = len(overdue_tasks) + len(overdue_events)
        
        embed.add_field(
            name="📊 Summary",
            value=f"Total Pending: {total_pending}\nOverdue: {total_overdue}\nDue Today: {len(today_tasks) + len(today_events)}",
            inline=True
        )
        
        embed.set_footer(text=f"Use commands in #{bot.get_channel(DISCORD_COMMANDS_CHANNEL_ID).name if bot.get_channel(DISCORD_COMMANDS_CHANNEL_ID) else 'commands-channel'} to manage items")
        
        # Send to Discord notifications channel
        channel = bot.get_channel(DISCORD_NOTIFICATIONS_CHANNEL_ID)
        if channel:
            await channel.send("@everyone", embed=embed)
            print("✅ Daily update sent successfully")
        else:
            print("⚠️ Discord notifications channel not found for daily update")
            
    except Exception as e:
        print(f"❌ Error sending daily update: {e}")
        import traceback
        traceback.print_exc()

def generate_recurring_instances(item, start_date=None, end_date=None):
    """Generate recurring event instances within a date range"""
    if item['repeat_interval'] == 'none':
        return [item]
    
    instances = []
    
    # Set default date range (6 months back to 2 years forward)
    if not start_date:
        start_date = datetime.now() - timedelta(days=180)
    if not end_date:
        end_date = datetime.now() + timedelta(days=730)
    
    # Make start_date and end_date timezone-naive for comparison
    if start_date.tzinfo is not None:
        start_date = start_date.replace(tzinfo=None)
    if end_date.tzinfo is not None:
        end_date = end_date.replace(tzinfo=None)
    
    try:
        original_date = datetime.fromisoformat(item['due_date'])
        # Make sure original_date is also timezone-naive
        if original_date.tzinfo is not None:
            original_date = original_date.replace(tzinfo=None)
    except:
        return [item]  # Return original if date parsing fails
    
    current_date = original_date
    instance_count = 0
    max_instances = 100  # Safety limit
    
    while current_date <= end_date and instance_count < max_instances:
        if current_date >= start_date:
            # Create instance
            instance = item.copy()
            instance['due_date'] = current_date.isoformat()
            instance['id'] = f"{item['id']}_recur_{instance_count}" if instance_count > 0 else item['id']
            instance['is_recurring_instance'] = instance_count > 0
            instance['original_id'] = item['id']
            instances.append(instance)
        
        instance_count += 1
        
        # Calculate next occurrence
        if item['repeat_interval'] == 'daily':
            current_date += timedelta(days=1)
        elif item['repeat_interval'] == 'weekly':
            current_date += timedelta(weeks=1)
        elif item['repeat_interval'] == 'monthly':
            current_date += relativedelta(months=1)
        else:
            break
    
    return instances

# ========================
# Scheduler Setup
# ========================
scheduler = AsyncIOScheduler()

def schedule_reminders_and_updates(loop_handle):
    """Schedule reminders 30 min before due AND daily updates."""
    print("🔄 Scheduling reminders and updates...")
    items = database.fetch_items()
    now = datetime.now()
    
    # Clear existing reminder jobs to avoid duplicates
    existing_jobs = scheduler.get_jobs()
    for job in existing_jobs:
        if job.id.startswith('reminder_'):
            scheduler.remove_job(job.id)
    
    # Schedule individual reminders
    reminder_count = 0
    for i in items:
        if i.get("status") != "pending":
            continue

        due_str = i.get("due_date")
        if not due_str:
            continue
        try:
            due = datetime.fromisoformat(due_str)
            if due.tzinfo:
                due = due.replace(tzinfo=None)
        except Exception:
            continue

        # Predefine users, change the values or add more if needed
        USER_ID_MAP = {
            "Discord User Name 1": Discord User ID 1,
            "Discord User Name 2": Discord User ID 2,
        }

        reminder_time = due - timedelta(minutes=30)
        if now <= reminder_time <= (now + timedelta(days=30)):  # Only schedule for next 30 days

            mention_name = i.get('mention')  # screen name from DB
            mention_text = ""
            if mention_name:
                user_id = USER_ID_MAP.get(mention_name)
                if user_id:
                    mention_text = f"<@{user_id}>"
                else:
                    mention_text = f"@{mention_name}"  # fallback to plain text

            message = (
                f"⏰ Reminder: {i.get('type', '').title()} '{i.get('name')}' "
                f"due at {format_time_12hour(i.get('due_date'))}{' ' + mention_text if mention_text else ''}\n"
                f"Priority: {i.get('priority')}\n"
                f"Notes: {i.get('notes') or 'No notes'}"
            )

            def create_reminder_job(msg=message):
                def job_function():
                    try:
                        asyncio.run_coroutine_threadsafe(notify_discord(msg, mention_everyone=True), loop_handle)
                    except Exception as e:
                        print("⚠️ Failed to send reminder notification:", e)
                return job_function

            job_id = f"reminder_{i.get('id')}_{int(reminder_time.timestamp())}"
            scheduler.add_job(create_reminder_job(), "date", run_date=reminder_time, id=job_id)
            reminder_count += 1

    # Schedule daily updates at 8:00 AM every day
    def daily_update_job():
        try:
            asyncio.run_coroutine_threadsafe(send_daily_update(), loop_handle)
        except Exception as e:
            print("⚠️ Failed to schedule daily update:", e)

    scheduler.add_job(
        daily_update_job,
        "cron",
        hour=8,
        minute=0,
        id="daily_update",
        replace_existing=True
    )

    if not scheduler.running:
        scheduler.start()
        
    print(f"✅ Scheduler configured - {reminder_count} reminders scheduled, daily updates at 8:00 AM")

# ========================
# Flask Setup
# ========================
app = Flask(__name__)
app.secret_key = SECRET_KEY

# Add custom Jinja2 filters
@app.template_filter('format_datetime')
def format_datetime_filter(datetime_str):
    """Format ISO datetime string for display in 12-hour format"""
    if not datetime_str or datetime_str == 'None':
        return ''
    try:
        dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %I:%M %p')
    except:
        return str(datetime_str).replace('T', ' ')

@app.template_filter('time_until_due')
def time_until_due_filter(datetime_str):
    """Calculate time until due date and return status"""
    if not datetime_str or datetime_str == 'None':
        return 'no-date'
    try:
        due_date = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        if due_date.tzinfo:
            due_date = due_date.replace(tzinfo=None)
        
        now = datetime.now()
        if due_date < now:
            return 'overdue'
        elif (due_date - now).days <= 1:
            return 'due-soon'
        else:
            return 'normal'
    except:
        return 'normal'

# ========================
# Flask Routes
# ========================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = database.fetch_user(username)
        if user and check_password_hash(user[2], password):
            session["user"] = username
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

def login_required(func):
    """Decorator to protect routes"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper

@app.route("/dashboard")
@login_required
def dashboard():
    try:
        items = database.fetch_items()
        return render_template("dashboard.html", items=items)
    except Exception as e:
        print(f"Error in dashboard route: {e}")
        return f"<h1>Dashboard Error</h1><p>Error: {str(e)}</p><p><a href='/logout'>Logout</a></p>", 500

@app.route("/add_item/<item_type>", methods=["GET", "POST"])
@login_required
def add_item_page(item_type):
    if request.method == "POST":
        # Process mention field - clean it up and ensure proper formatting
        mention = request.form.get("mention", "").strip()
        # Remove @everyone if it was accidentally added, keep other mentions
        if mention.lower() == "@everyone":
            mention = ""
        # Don't add @ symbol here - store as entered, format when sending to Discord
        
        new_id = database.add_item_db(
            item_type,
            request.form["name"],
            request.form["due_date"],
            mention,
            request.form.get("repeat_interval", "none"),
            request.form.get("category", "Misc"),
            request.form.get("notes", ""),
            request.form.get("priority", "Medium"),
            request.form.get("color", "#3399ff")
        )

        repeat_text = f" (Repeats {request.form.get('repeat_interval', 'none')})" if request.form.get('repeat_interval', 'none') != 'none' else ""
        message = (
            f"🆕 New {item_type.title()} Created!{repeat_text}\n\n"
            f"**{request.form['name']}**\n"
            f"📅 Due: {format_time_12hour(request.form['due_date'])}\n"
            f"🎯 Priority: {request.form.get('priority','Medium')}\n"
            f"📂 Category: {request.form.get('category', 'Misc')}\n"
            f"📝 Notes: {request.form.get('notes','None')}\n"
            f"🆔 ID: {new_id}"
        )

        if ASYNC_LOOP and BOT_READY:
            # Send with @everyone mention for new creations
            asyncio.run_coroutine_threadsafe(notify_discord(message, mention_everyone=True), ASYNC_LOOP)
            # Reschedule reminders after new item is added (run in thread pool)
            executor.submit(schedule_reminders_and_updates, ASYNC_LOOP)
        else:
            print(f"⚠️ Discord not available, message would be: {message}")

        return redirect("/dashboard")

    return render_template("add_item.html", item_type=item_type)

@app.route("/complete/<int:item_id>")
@login_required
def complete_item(item_id):
    # Get item info before update for notification
    item = database.get_item(item_id)
    if not item:
        return redirect("/dashboard")
    
    # Update status in database - use the corrected function
    try:
        database.update_item_status(item_id, "completed")
    except Exception as e:
        print(f"Error updating item status: {e}")
        # Try alternative update method if the main one fails
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET status=? WHERE id=?", ("completed", item_id))
        conn.commit()
        conn.close()
    
    mention = format_mention_for_discord(item[5]) if item[5] else ""
    message = (
        f"✅ {item[1].title()} Completed!\n\n"
        f"**{item[2]}**{' ' + mention if mention else ''}\n"
        f"🎯 Priority: {item[9]}\n"
        f"📝 Notes: {item[8] or 'None'}"
    )

    if ASYNC_LOOP and BOT_READY:
        asyncio.run_coroutine_threadsafe(notify_discord(message, mention_everyone=True), ASYNC_LOOP)
    else:
        print(f"⚠️ Discord not available, message would be: {message}")
    return redirect("/dashboard")

@app.route("/calendar")
@login_required
def calendar():
    return render_template("calendar.html")

@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html")

@app.route("/api/events", methods=["GET", "POST"])
@login_required
def api_events():
    try:
        print("API /events called")
        if request.method == "GET":
            items = database.fetch_items()
            print(f"Fetched {len(items)} items from database")
            events = []
            
            # Get date range from query parameters
            start_param = request.args.get('start')
            end_param = request.args.get('end')
            print(f"Date range: {start_param} to {end_param}")
            
            try:
                start_date = datetime.fromisoformat(start_param.replace('Z', '')) if start_param else None
                end_date = datetime.fromisoformat(end_param.replace('Z', '')) if end_param else None
            except Exception as e:
                print(f"Date parsing error: {e}")
                start_date = None
                end_date = None
            
            for item in items:
                try:
                    # Generate recurring instances
                    instances = generate_recurring_instances(item, start_date, end_date)
                    print(f"Generated {len(instances)} instances for item {item['id']}")
                    
                    for instance in instances:
                        events.append({
                            "id": instance["id"],
                            "title": instance["name"],
                            "start": instance["due_date"],
                            "color": instance.get("color") or "#3399ff",
                            "extendedProps": {
                                "type": instance["type"],
                                "status": instance["status"],
                                "priority": instance["priority"],
                                "category": instance["category"],
                                "notes": instance["notes"],
                                "mention": instance["mention"],
                                "repeat_interval": instance["repeat_interval"],
                                "is_recurring_instance": instance.get("is_recurring_instance", False),
                                "original_id": instance.get("original_id", instance["id"])
                            }
                        })
                except Exception as e:
                    print(f"Error processing item {item['id']}: {e}")
                    # Add the original item without recurring instances
                    events.append({
                        "id": item["id"],
                        "title": item["name"],
                        "start": item["due_date"],
                        "color": item.get("color") or "#3399ff",
                        "extendedProps": {
                            "type": item["type"],
                            "status": item["status"],
                            "priority": item["priority"],
                            "category": item["category"],
                            "notes": item["notes"],
                            "mention": item["mention"],
                            "repeat_interval": item["repeat_interval"],
                            "is_recurring_instance": False,
                            "original_id": item["id"]
                        }
                    })
            
            print(f"Returning {len(events)} events")
            return jsonify(events)
            
        else:  # POST
            data = request.get_json() or {}
            new_id = database.add_item_db(
                data.get("type", "event"),
                data.get("title", "Untitled"),
                data.get("start"),
                data.get("mention", ""),
                data.get("repeat_interval", "none"),
                data.get("category", "Misc"),
                data.get("notes", ""),
                data.get("priority", "Medium"),
                data.get("color", "#3399ff")
            )
            # Reschedule reminders after new item
            if ASYNC_LOOP:
                executor.submit(schedule_reminders_and_updates, ASYNC_LOOP)
            return jsonify({"id": new_id}), 201
            
    except Exception as e:
        print(f"ERROR in /api/events: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/events/<int:event_id>", methods=["GET", "PUT", "DELETE"])
@login_required
def api_event(event_id):
    if request.method == "GET":
        item = database.get_item(event_id)
        if not item:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "id": item[0],
            "type": item[1],
            "name": item[2],
            "due_date": item[3],
            "status": item[4],
            "mention": item[5],
            "repeat_interval": item[6],
            "category": item[7],
            "notes": item[8],
            "priority": item[9],
            "color": item[10] or "#3399ff"
        })

    elif request.method == "PUT":
        data = request.get_json() or {}
        allowed = {"type","name","due_date","status","mention","repeat_interval","category","notes","priority","color"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"error": "no valid fields provided"}), 400
        
        # Get item info before update for notification
        item = database.get_item(event_id)
        database.update_item_db(event_id, **updates)
        
        # Send notification if status changed to completed
        if "status" in updates and updates["status"] == "completed" and item:
            if ASYNC_LOOP and BOT_READY:
                mention = format_mention_for_discord(item[5]) if item[5] else ""
                message = (
                    f"✅ {item[1].title()} Completed!\n\n"
                    f"**{item[2]}**{' ' + mention if mention else ''}\n"
                    f"🎯 Priority: {item[9]}\n"
                    f"📝 Notes: {item[8] or 'None'}"
                )
                asyncio.run_coroutine_threadsafe(notify_discord(message, mention_everyone=True), ASYNC_LOOP)
        
        # Reschedule reminders after update
        if ASYNC_LOOP:
            executor.submit(schedule_reminders_and_updates, ASYNC_LOOP)
        
        return jsonify({"status": "ok"})

    else:  # DELETE
        # Get item info before deletion for notification
        item = database.get_item(event_id)
        
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id=?", (event_id,))
        conn.commit()
        conn.close()
        
        # Send notification about deletion
        if item and ASYNC_LOOP and BOT_READY:
            message = (
                f"🗑️ {item[1].title()} Deleted!\n\n"
                f"**{item[2]}** has been removed from the system."
            )
            asyncio.run_coroutine_threadsafe(notify_discord(message, mention_everyone=True), ASYNC_LOOP)
        
        return jsonify({"status": "deleted"})

@app.route("/status")
def status():
    """Status endpoint to check if Discord bot is connected"""
    return jsonify({
        "web_app": "running",
        "discord_bot": "connected" if BOT_READY else "disconnected",
        "database": "connected"
    })

# Manual trigger for daily update (for testing)
@app.route("/trigger_daily_update")
@login_required 
def trigger_daily_update():
    """Manual trigger for daily update - useful for testing"""
    if ASYNC_LOOP and BOT_READY:
        asyncio.run_coroutine_threadsafe(send_daily_update(), ASYNC_LOOP)
        return "Daily update triggered!"
    else:
        return "Discord bot not ready"

# ========================
# Run Flask in a thread
# ========================
def run_flask():
    print("🌐 Starting Flask web application...")
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

# ========================
# Discord Bot Runner
# ========================
async def run_discord_bot():
    """Try to run the Discord bot with error handling"""
    try:
        print("🤖 Starting Discord bot...")
        await bot.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("❌ Discord bot login failed - Invalid token")
        print("💡 The web application will still work, but Discord features will be unavailable")
        print("🔧 To fix: Update your DISCORD_TOKEN in config.py")
    except Exception as e:
        print(f"❌ Discord bot error: {e}")
        print("💡 The web application will still work, but Discord features will be unavailable")

# ========================
# Main Async Entry
# ========================
async def main():
    global ASYNC_LOOP
    print("🚀 Starting Tasks & Events Management System...")
    
    # Initialize database
    print("💾 Initializing database...")
    database.init_db()
    
    # Set up async loop
    ASYNC_LOOP = asyncio.get_running_loop()
    
    # Start scheduler
    print("⏰ Starting reminder scheduler...")
    schedule_reminders_and_updates(ASYNC_LOOP)
    
    # Start Flask in thread
    print("🌐 Starting web server...")
    Thread(target=run_flask, daemon=True).start()
    
    # Give Flask a moment to start
    await asyncio.sleep(2)
    print("✅ Web application running at:")
    print("   - Local: http://127.0.0.1:5000")
    print("   - Network: http://192.168.0.21:5000")
    print("   - Manual daily update: http://127.0.0.1:5000/trigger_daily_update")
    
    # Try to start Discord bot
    await run_discord_bot()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Shutting down gracefully...")
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()