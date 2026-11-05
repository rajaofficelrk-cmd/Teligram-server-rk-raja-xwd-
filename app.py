from flask import Flask, render_template_string, request, jsonify
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
import asyncio
import os
import random
import threading
import time
from datetime import datetime
import logging
import nest_asyncio

# Apply nest_asyncio for Render compatibility
nest_asyncio.apply()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'telegram_bot_secret_key_123456')

# For Render deployment
if 'RENDER' in os.environ:
    port = int(os.environ.get('PORT', 10000))
else:
    port = 5000

# Global variables
sending_active = False
current_status = "Not Started"
sent_count = 0
failed_count = 0
logs = []

class TelegramBot:
    def __init__(self):
        self.api_id = None
        self.api_hash = None
        self.client = None
        self.is_connected = False
        self.loop = None
        self.need_verification = False
        self.need_password = False
        
    async def _initialize(self, phone_number, api_id, api_hash):
        """Initialize Telegram client"""
        try:
            self.api_id = int(api_id)
            self.api_hash = api_hash
            session_name = f'session_{phone_number}'
            self.client = TelegramClient(session_name, self.api_id, self.api_hash)
            
            # Start connection without code first to trigger verification
            await self.client.connect()
            
            # Send code request
            result = await self.client.send_code_request(phone_number)
            self.need_verification = True
            return True, "Verification code sent to your Telegram app!"
            
        except SessionPasswordNeededError:
            self.need_password = True
            return False, "2FA password required"
        except Exception as e:
            return False, f"Login failed: {str(e)}"
    
    def initialize(self, phone_number, api_id, api_hash):
        """Initialize with dedicated event loop"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            success, message = self.loop.run_until_complete(
                self._initialize(phone_number, api_id, api_hash)
            )
            return success, message
        except Exception as e:
            return False, f"Initialization error: {str(e)}"
    
    async def _verify_code(self, code):
        """Verify code and complete login"""
        try:
            if not self.client:
                return False, "Client not initialized"
                
            # Sign in with code
            await self.client.sign_in(code=code)
            self.is_connected = True
            self.need_verification = False
            return True, "Login successful!"
            
        except SessionPasswordNeededError:
            self.need_password = True
            return False, "2FA password required"
        except Exception as e:
            return False, f"Verification failed: {str(e)}"
    
    def verify_code(self, code):
        """Verify code using dedicated loop"""
        try:
            if not self.loop:
                return False, "No active session"
            
            result = self.loop.run_until_complete(self._verify_code(code))
            return result
        except Exception as e:
            return False, f"Verification error: {str(e)}"
    
    async def _approve_login(self):
        """Approve login from another device"""
        try:
            if not self.client:
                return False, "Client not initialized"
                
            # This will wait for approval from another device
            await self.client.sign_in(phone=self.phone_number)
            self.is_connected = True
            return True, "Login approved successfully!"
            
        except Exception as e:
            return False, f"Approval failed: {str(e)}"
    
    def approve_login(self, phone_number):
        """Approve login using dedicated loop"""
        try:
            self.phone_number = phone_number
            if not self.loop:
                return False, "No active session"
            
            result = self.loop.run_until_complete(self._approve_login())
            return result
        except Exception as e:
            return False, f"Approval error: {str(e)}"
    
    async def _send_message(self, target, message):
        """Send message to target"""
        try:
            entity = await self.client.get_entity(target)
            await self.client.send_message(entity, message)
            return True, "Message sent successfully"
        except Exception as e:
            return False, str(e)
    
    def send_message(self, target, message):
        """Send message using dedicated loop"""
        try:
            if not self.loop or not self.is_connected:
                return False, "Not connected to Telegram"
            
            result = self.loop.run_until_complete(
                self._send_message(target, message)
            )
            return result
        except Exception as e:
            return False, f"Send error: {str(e)}"
    
    async def _get_contacts(self):
        """Get all dialogs/contacts"""
        try:
            dialogs = await self.client.get_dialogs()
            contacts = []
            for dialog in dialogs:
                entity = dialog.entity
                if entity:
                    contact_info = {
                        'name': dialog.name,
                        'type': type(entity).__name__
                    }
                    
                    # Get identifier
                    if hasattr(entity, 'username') and entity.username:
                        contact_info['identifier'] = f"@{entity.username}"
                    elif hasattr(entity, 'id'):
                        # For groups/channels, add minus sign for IDs
                        if hasattr(entity, 'megagroup') and entity.megagroup:
                            contact_info['identifier'] = f"-100{entity.id}"
                        else:
                            contact_info['identifier'] = f"{entity.id}"
                    else:
                        contact_info['identifier'] = "Unknown"
                    
                    contacts.append(contact_info)
            return True, contacts
        except Exception as e:
            return False, str(e)
    
    def get_contacts(self):
        """Get contacts using dedicated loop"""
        try:
            if not self.loop or not self.is_connected:
                return False, "Not connected to Telegram"
            
            result = self.loop.run_until_complete(self._get_contacts())
            return result
        except Exception as e:
            return False, f"Contacts error: {str(e)}"

def add_log(message, type="info"):
    """Add log with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        "time": timestamp,
        "message": message,
        "type": type
    }
    logs.append(log_entry)
    # Keep only last 100 logs
    if len(logs) > 100:
        logs.pop(0)
    print(f"[{timestamp}] {message}")

def load_messages_from_file(file_content):
    """Load messages from file content"""
    try:
        if hasattr(file_content, 'read'):
            content = file_content.read().decode('utf-8')
        else:
            content = file_content
        
        messages = [line.strip() for line in content.split('\n') if line.strip()]
        return messages
    except Exception as e:
        add_log(f"Error loading messages: {str(e)}", "error")
        return []

def apply_prefix_to_messages(messages, prefix):
    """Apply prefix to all messages"""
    if not prefix or not prefix.strip():
        return messages
    
    prefix = prefix.strip()
    prefixed_messages = []
    
    for message in messages:
        if message.strip():
            prefixed_messages.append(f"{prefix} {message}")
    
    return prefixed_messages

def sending_loop(inbox_targets, group_targets, messages, delay_seconds, prefix):
    """Main sending loop"""
    global sending_active, current_status, sent_count, failed_count
    
    sent_count = 0
    failed_count = 0
    
    # Apply prefix to messages
    if prefix and prefix.strip():
        messages = apply_prefix_to_messages(messages, prefix.strip())
        add_log(f"🔤 Prefix applied: '{prefix}'", "info")
    
    add_log("🚀 Sending process started!", "success")
    add_log(f"📧 Inbox Targets: {len(inbox_targets)} | 🏠 Group Targets: {len(group_targets)} | 💬 Messages: {len(messages)}", "info")
    
    all_targets = inbox_targets + group_targets
    
    if not all_targets:
        add_log("❌ No targets provided", "error")
        return
        
    if not messages:
        add_log("❌ No messages provided", "error")
        return
    
    message_index = 0
    
    while sending_active:
        for target in all_targets:
            if not sending_active:
                break
                
            if not target.strip():
                continue
                
            # Get current message
            message = messages[message_index % len(messages)]
            
            # Send message
            success, result = bot.send_message(target.strip(), message)
            
            if success:
                sent_count += 1
                # Show message content in logs
                add_log(f"✅ Sent to {target}: \"{message}\"", "success")
            else:
                failed_count += 1
                add_log(f"❌ Failed to send to {target}: {result}", "error")
            
            # Update status
            current_status = f"Sent: {sent_count} | Failed: {failed_count}"
            
            # Delay
            add_log(f"⏳ Waiting {delay_seconds} seconds for next message...", "info")
            time.sleep(delay_seconds)
            
            message_index += 1
    
    add_log("🛑 Sending stopped", "info")
    current_status = f"Stopped | Sent: {sent_count} | Failed: {failed_count}"

def start_sending_async(inbox_targets, group_targets, messages, delay_seconds, prefix):
    """Start sending in separate thread"""
    try:
        sending_loop(inbox_targets, group_targets, messages, delay_seconds, prefix)
    except Exception as e:
        add_log(f"Error in sending loop: {str(e)}", "error")
        global current_status
        current_status = f"Error: {str(e)}"

bot = TelegramBot()

# HTML Template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RK RAJA Telegram Message Sender</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh; padding: 20px; 
        }
        .container { 
            max-width: 1200px; margin: 0 auto; background: white; 
            border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            overflow: hidden; 
        }
        .header { 
            background: linear-gradient(135deg, #2c3e50, #34495e); 
            color: white; padding: 30px; text-align: center; 
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header p { opacity: 0.9; font-size: 1.1em; }
        .content { 
            display: grid; grid-template-columns: 1fr 1fr; gap: 30px; 
            padding: 30px; 
        }
        @media (max-width: 768px) { .content { grid-template-columns: 1fr; } }
        .card { 
            background: #f8f9fa; border-radius: 10px; padding: 25px; 
            border: 1px solid #e9ecef; 
        }
        .card h2 { 
            color: #2c3e50; margin-bottom: 20px; font-size: 1.5em; 
            border-bottom: 2px solid #3498db; padding-bottom: 10px; 
        }
        .form-group { margin-bottom: 20px; }
        label { 
            display: block; margin-bottom: 8px; font-weight: 600; 
            color: #2c3e50; 
        }
        input, textarea, select { 
            width: 100%; padding: 12px; border: 2px solid #ddd; 
            border-radius: 8px; font-size: 14px; transition: border-color 0.3s; 
        }
        input:focus, textarea:focus, select:focus { 
            outline: none; border-color: #3498db; 
        }
        textarea { min-height: 80px; resize: vertical; }
        .btn { 
            background: linear-gradient(135deg, #3498db, #2980b9);
            color: white; border: none; padding: 12px 25px; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer; 
            transition: all 0.3s; width: 100%; margin-top: 10px; 
        }
        .btn:hover { 
            transform: translateY(-2px); 
            box-shadow: 0 10px 20px rgba(52, 152, 219, 0.3); 
        }
        .btn-stop { 
            background: linear-gradient(135deg, #e74c3c, #c0392b); 
        }
        .btn-success { 
            background: linear-gradient(135deg, #27ae60, #229954); 
        }
        .btn-warning { 
            background: linear-gradient(135deg, #f39c12, #e67e22); 
        }
        .btn-contacts { 
            background: linear-gradient(135deg, #9b59b6, #8e44ad); 
        }
        .status-card { 
            background: linear-gradient(135deg, #34495e, #2c3e50); 
            color: white; grid-column: 1 / -1; 
        }
        .status-item { 
            display: flex; justify-content: space-between; margin-bottom: 15px; 
            padding: 15px; background: rgba(255,255,255,0.1); 
            border-radius: 8px; 
        }
        .status-value { font-weight: 600; color: #3498db; }
        .alert { 
            padding: 15px; border-radius: 8px; margin-bottom: 20px; 
            display: none; 
        }
        .alert-success { 
            background: #d4edda; color: #155724; border: 1px solid #c3e6cb; 
        }
        .alert-error { 
            background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; 
        }
        .alert-warning { 
            background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; 
        }
        .logs-container { 
            max-height: 400px; overflow-y: auto; background: #2c3e50; 
            border-radius: 8px; padding: 15px; color: white; 
            font-family: monospace; font-size: 12px; 
        }
        .contacts-container { 
            max-height: 400px; overflow-y: auto; background: white; 
            border-radius: 8px; border: 1px solid #ddd; padding: 15px; 
        }
        .log-entry { margin-bottom: 8px; padding: 5px; border-radius: 4px; }
        .log-success { background: #27ae60; color: white; }
        .log-error { background: #e74c3c; color: white; }
        .log-info { background: #3498db; color: white; }
        .contact-item { 
            padding: 10px; border-bottom: 1px solid #eee; 
            cursor: pointer; transition: background 0.3s;
        }
        .contact-item:hover { background: #f8f9fa; }
        .contact-item:last-child { border-bottom: none; }
        .verification-box {
            background: #fff3cd; border: 2px solid #ffeaa7; 
            padding: 20px; border-radius: 10px; margin: 15px 0;
        }
        .tab-container { margin-bottom: 20px; }
        .tab-buttons { display: flex; margin-bottom: 10px; }
        .tab-btn { 
            padding: 10px 20px; background: #ecf0f1; border: none; 
            cursor: pointer; margin-right: 5px; border-radius: 5px 5px 0 0;
        }
        .tab-btn.active { background: #3498db; color: white; }
        .tab-content { display: none; padding: 15px; background: white; border-radius: 0 5px 5px 5px; }
        .tab-content.active { display: block; }
        .prefix-example {
            background: #e8f4fd; border: 1px solid #3498db; 
            padding: 10px; border-radius: 5px; margin-top: 5px;
            font-size: 12px; color: #2c3e50;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 RK RAJA TELIGRAM SERVER 👍</h1>
            <p>Send messages to Telegram users and groups automatically</p>
        </div>
        
        <div class="content">
            <!-- Login Section -->
            <div class="card">
                <h2>🔐 Telegram Login</h2>
                <div id="loginAlert" class="alert"></div>
                
                <div class="form-group">
                    <label for="api_id">API ID:</label>
                    <input type="text" id="api_id" placeholder="Enter your API ID" value="26260543">
                </div>
                <div class="form-group">
                    <label for="api_hash">API Hash:</label>
                    <input type="text" id="api_hash" placeholder="Enter your API Hash" value="cd6806940a66a4a4545bd660f93f3e27">
                </div>
                <div class="form-group">
                    <label for="phone_number">Phone Number:</label>
                    <input type="text" id="phone_number" placeholder="+919876543210">
                </div>
                
                <button class="btn" onclick="startLogin()">📱 Send Login Code</button>
                <button class="btn btn-contacts" onclick="getContacts()">📞 Get Contacts</button>
                
                <div id="verificationBox" class="verification-box" style="display: none;">
                    <h3>📲 Enter Verification Code</h3>
                    <p>We sent a code to your Telegram app. Enter it below:</p>
                    <div class="form-group">
                        <input type="text" id="verificationCode" placeholder="Enter 5-digit code" maxlength="5">
                    </div>
                    <button class="btn btn-success" onclick="verifyCode()">✅ Verify Code</button>
                    <button class="btn btn-warning" onclick="approveLogin()" style="margin-top: 10px;">👍 Yes, It's Me - Approve Login</button>
                </div>
            </div>
            
            <!-- Configuration Section -->
            <div class="card">
                <h2>⚙️ Send Configuration</h2>
                <div id="configAlert" class="alert"></div>
                
                <div class="tab-container">
                    <div class="tab-buttons">
                        <button class="tab-btn active" onclick="openTab('inboxTab')">📧 Inbox Targets</button>
                        <button class="tab-btn" onclick="openTab('groupTab')">🏠 Group Targets</button>
                        <button class="tab-btn" onclick="openTab('messagesTab')">💬 Messages</button>
                    </div>
                    
                    <div id="inboxTab" class="tab-content active">
                        <div class="form-group">
                            <label for="inbox_targets">Inbox Targets (usernames or phone numbers, one per line):</label>
                            <textarea id="inbox_targets" placeholder="@username1&#10;+919876543210&#10;@username2">@itxthedevil</textarea>
                        </div>
                    </div>
                    
                    <div id="groupTab" class="tab-content">
                        <div class="form-group">
                            <label for="group_targets">Group Targets (group usernames or IDs, one per line):</label>
                            <textarea id="group_targets" placeholder="@group1&#10;-1001234567890&#10;@group2"></textarea>
                        </div>
                    </div>
                    
                    <div id="messagesTab" class="tab-content">
                        <div class="form-group">
                            <label for="messages_file">Upload Messages File (TXT):</label>
                            <input type="file" id="messages_file" accept=".txt">
                            <small>Text file with one message per line</small>
                        </div>
                        
                        <div class="form-group">
                            <label for="message_prefix">Message Prefix (Optional):</label>
                            <input type="text" id="message_prefix" placeholder="ramesh">
                            <div class="prefix-example">
                                <strong>Example:</strong><br>
                                Prefix: "ramesh" + Message: "tu chutiya h"<br>
                                <strong>Result:</strong> "ramesh tu chutiya h"
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="form-group">
                    <label for="delay_seconds">Delay Between Messages (seconds):</label>
                    <input type="number" id="delay_seconds" value="40" min="30" max="300">
                </div>
                
                <button class="btn" onclick="startSending()">🚀 Start Sending</button>
                <button class="btn btn-stop" onclick="stopSending()">🛑 Stop Sending</button>
            </div>
            
            <!-- Status Section -->
            <div class="card status-card">
                <h2>📊 Sending Status</h2>
                <div class="status-item">
                    <span>Status:</span>
                    <span id="statusValue" class="status-value">Not Started</span>
                </div>
                <div class="status-item">
                    <span>Messages Sent:</span>
                    <span id="sentCount" class="status-value">0</span>
                </div>
                <div class="status-item">
                    <span>Messages Failed:</span>
                    <span id="failedCount" class="status-value">0</span>
                </div>
            </div>
            
            <!-- Live Logs Section -->
            <div class="card">
                <h2>📝 Live Logs</h2>
                <div id="logsContainer" class="logs-container">
                    <div class="log-entry log-info">System started - Ready to send messages</div>
                </div>
            </div>

            <!-- Contacts Section -->
            <div class="card">
                <h2>📞 Available Contacts & Groups</h2>
                <div id="contactsContainer" class="contacts-container">
                    <div style="text-align: center; color: #7f8c8d; padding: 20px;">
                        Click "Get Contacts" to load your Telegram contacts and groups
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let isLoggedIn = false;
        
        function openTab(tabName) {
            const tabContents = document.getElementsByClassName('tab-content');
            for (let tab of tabContents) {
                tab.classList.remove('active');
            }
            
            const tabButtons = document.getElementsByClassName('tab-btn');
            for (let btn of tabButtons) {
                btn.classList.remove('active');
            }
            
            document.getElementById(tabName).classList.add('active');
            event.currentTarget.classList.add('active');
        }
        
        function showAlert(elementId, message, type) {
            const alert = document.getElementById(elementId);
            alert.textContent = message;
            alert.className = `alert alert-${type}`;
            alert.style.display = 'block';
            setTimeout(() => {
                alert.style.display = 'none';
            }, 5000);
        }
        
        async function startLogin() {
            const api_id = document.getElementById('api_id').value;
            const api_hash = document.getElementById('api_hash').value;
            const phone_number = document.getElementById('phone_number').value;
            
            if (!api_id || !api_hash || !phone_number) {
                showAlert('loginAlert', 'Please fill all login fields', 'error');
                return;
            }
            
            try {
                const response = await fetch('/start_login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        api_id: api_id,
                        api_hash: api_hash,
                        phone_number: phone_number
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert('loginAlert', '✅ ' + data.message, 'success');
                    document.getElementById('verificationBox').style.display = 'block';
                } else {
                    showAlert('loginAlert', '❌ ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('loginAlert', '❌ Login failed: ' + error, 'error');
            }
        }
        
        async function verifyCode() {
            const code = document.getElementById('verificationCode').value;
            
            if (!code) {
                showAlert('loginAlert', 'Please enter verification code', 'error');
                return;
            }
            
            try {
                const response = await fetch('/verify_code', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        code: code
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert('loginAlert', '✅ ' + data.message, 'success');
                    document.getElementById('verificationBox').style.display = 'none';
                    isLoggedIn = true;
                } else {
                    showAlert('loginAlert', '❌ ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('loginAlert', '❌ Verification failed: ' + error, 'error');
            }
        }
        
        async function approveLogin() {
            const phone_number = document.getElementById('phone_number').value;
            
            try {
                const response = await fetch('/approve_login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        phone_number: phone_number
                    })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert('loginAlert', '✅ ' + data.message, 'success');
                    document.getElementById('verificationBox').style.display = 'none';
                    isLoggedIn = true;
                } else {
                    showAlert('loginAlert', '❌ ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('loginAlert', '❌ Approval failed: ' + error, 'error');
            }
        }
        
        async function getContacts() {
            if (!isLoggedIn) {
                showAlert('loginAlert', '❌ Please login to Telegram first', 'error');
                return;
            }
            
            try {
                const response = await fetch('/get_contacts');
                const data = await response.json();
                
                const contactsContainer = document.getElementById('contactsContainer');
                
                if (data.success) {
                    if (data.contacts.length === 0) {
                        contactsContainer.innerHTML = '<div style="text-align: center; color: #7f8c8d; padding: 20px;">No contacts found</div>';
                        return;
                    }
                    
                    let html = '';
                    data.contacts.forEach(contact => {
                        html += `
                            <div class="contact-item" onclick="copyToClipboard('${contact.identifier}')">
                                <div>
                                    <strong>${contact.name}</strong><br>
                                    <small>${contact.identifier} • ${contact.type}</small>
                                </div>
                            </div>
                        `;
                    });
                    contactsContainer.innerHTML = html;
                    showAlert('loginAlert', '✅ Contacts loaded successfully! Click any contact to copy ID', 'success');
                } else {
                    showAlert('loginAlert', '❌ ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('loginAlert', '❌ Failed to get contacts: ' + error, 'error');
            }
        }
        
        function copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(() => {
                showAlert('loginAlert', '✅ Copied: ' + text, 'success');
            });
        }
        
        async function startSending() {
            if (!isLoggedIn) {
                showAlert('configAlert', '❌ Please login to Telegram first', 'error');
                return;
            }
            
            const inbox_targets = document.getElementById('inbox_targets').value;
            const group_targets = document.getElementById('group_targets').value;
            const delay_seconds = document.getElementById('delay_seconds').value;
            const message_prefix = document.getElementById('message_prefix').value;
            
            const fileInput = document.getElementById('messages_file');
            if (!fileInput.files[0]) {
                showAlert('configAlert', '❌ Please upload messages file', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('inbox_targets', inbox_targets);
            formData.append('group_targets', group_targets);
            formData.append('delay_seconds', delay_seconds);
            formData.append('message_prefix', message_prefix);
            formData.append('messages_file', fileInput.files[0]);
            
            try {
                const response = await fetch('/start_sending', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert('configAlert', '🚀 ' + data.message, 'success');
                    updateStatus();
                    updateLogs();
                } else {
                    showAlert('configAlert', '❌ ' + data.message, 'error');
                }
            } catch (error) {
                showAlert('configAlert', '❌ Failed to start sending: ' + error, 'error');
            }
        }
        
        async function stopSending() {
            try {
                const response = await fetch('/stop_sending', {
                    method: 'POST'
                });
                
                const data = await response.json();
                showAlert('configAlert', '🛑 ' + data.message, 'success');
            } catch (error) {
                showAlert('configAlert', '❌ Failed to stop sending: ' + error, 'error');
            }
        }
        
        async function updateStatus() {
            try {
                const response = await fetch('/get_status');
                const data = await response.json();
                
                document.getElementById('statusValue').textContent = data.status;
                document.getElementById('sentCount').textContent = data.sent;
                document.getElementById('failedCount').textContent = data.failed;
                
                if (data.active) {
                    setTimeout(updateStatus, 1000);
                }
            } catch (error) {
                console.error('Status update failed:', error);
            }
        }
        
        async function updateLogs() {
            try {
                const response = await fetch('/get_logs');
                const data = await response.json();
                
                const logsContainer = document.getElementById('logsContainer');
                let html = '';
                
                data.logs.forEach(log => {
                    html += `<div class="log-entry log-${log.type}">[${log.time}] ${log.message}</div>`;
                });
                
                logsContainer.innerHTML = html;
                logsContainer.scrollTop = logsContainer.scrollHeight;
                
                setTimeout(updateLogs, 2000);
            } catch (error) {
                console.error('Logs update failed:', error);
            }
        }
        
        // Auto-fill API credentials
        document.getElementById('api_id').value = '26260543';
        document.getElementById('api_hash').value = 'cd6806940a66a4a4545bd660f93f3e27';
        document.getElementById('inbox_targets').value = '@itxthedevil';
        
        // Initial updates
        updateStatus();
        updateLogs();
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/start_login', methods=['POST'])
def start_login():
    try:
        data = request.json
        phone_number = data['phone_number']
        api_id = data['api_id']
        api_hash = data['api_hash']
        
        global bot
        bot = TelegramBot()
        
        success, message = bot.initialize(phone_number, api_id, api_hash)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/verify_code', methods=['POST'])
def verify_code():
    try:
        data = request.json
        code = data['code']
        
        global bot
        if not bot:
            return jsonify({'success': False, 'message': 'Login session not found'})
        
        success, message = bot.verify_code(code)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/approve_login', methods=['POST'])
def approve_login():
    try:
        data = request.json
        phone_number = data['phone_number']
        
        global bot
        if not bot:
            return jsonify({'success': False, 'message': 'Login session not found'})
        
        success, message = bot.approve_login(phone_number)
        return jsonify({'success': success, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/get_contacts', methods=['GET'])
def get_contacts():
    """Get available contacts"""
    try:
        if not bot or not bot.is_connected:
            return jsonify({'success': False, 'message': 'Not connected to Telegram. Please login first.'})
        
        success, result = bot.get_contacts()
        
        if success:
            return jsonify({'success': True, 'contacts': result})
        else:
            return jsonify({'success': False, 'message': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/start_sending', methods=['POST'])
def start_sending():
    global sending_active
    
    try:
        # Get form data
        inbox_targets_text = request.form['inbox_targets']
        group_targets_text = request.form['group_targets']
        delay_seconds = int(request.form['delay_seconds'])
        message_prefix = request.form['message_prefix']
        
        # Process targets
        inbox_targets = [t.strip() for t in inbox_targets_text.split('\n') if t.strip()]
        group_targets = [t.strip() for t in group_targets_text.split('\n') if t.strip()]
        
        # Process messages file
        messages = []
        if 'messages_file' in request.files and request.files['messages_file'].filename:
            file = request.files['messages_file']
            messages = load_messages_from_file(file)
        
        if not inbox_targets and not group_targets:
            return jsonify({'success': False, 'message': 'No targets provided'})
        
        if not messages:
            return jsonify({'success': False, 'message': 'No messages provided'})
        
        if delay_seconds < 30:
            return jsonify({'success': False, 'message': 'Delay should be at least 30 seconds'})
        
        # Check if bot is connected
        if not bot or not bot.is_connected:
            return jsonify({'success': False, 'message': 'Please login to Telegram first'})
        
        # Start sending in background thread
        sending_active = True
        thread = threading.Thread(
            target=start_sending_async,
            args=(inbox_targets, group_targets, messages, delay_seconds, message_prefix)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True, 
            'message': f'Sending started! Inbox: {len(inbox_targets)}, Groups: {len(group_targets)}, Messages: {len(messages)}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/stop_sending', methods=['POST'])
def stop_sending():
    global sending_active
    sending_active = False
    add_log("Sending stopped by user", "info")
    return jsonify({'success': True, 'message': 'Sending stopped successfully'})

@app.route('/get_status', methods=['GET'])
def get_status():
    global sent_count, failed_count, sending_active
    
    return jsonify({
        'status': current_status,
        'sent': sent_count,
        'failed': failed_count,
        'active': sending_active
    })

@app.route('/get_logs', methods=['GET'])
def get_logs():
    return jsonify({'logs': logs})

if __name__ == '__main__':
    if 'RENDER' in os.environ:
        print("🚀 Starting on Render...")
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        print("🚀 Starting locally...")
        print("📧 Open: http://localhost:5000")
        app.run(debug=True, host='0.0.0.0', port=5000)
