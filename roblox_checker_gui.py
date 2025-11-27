import sys, requests, asyncio, random, string, traceback, json
from datetime import datetime
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import QFont

try:
    from DrissionPage import Chromium, ChromiumOptions, errors
    DRISSION_AVAILABLE = True
except ImportError:
    DRISSION_AVAILABLE = False

# ------------------- Checker Thread ------------------- #
class Checker(QThread):
    update = pyqtSignal(str)
    pupdate = pyqtSignal(int)
    count = 0

    def __init__(self, usernames, webhook_url=None, debug=False, auto_signup=False, signup_password=None):
        super().__init__()
        self.usernames = usernames
        self.webhook_url = webhook_url
        self.running = True
        self.debug = debug
        self.consecutive_errors = 0
        self.max_errors_before_pause = 3
        self.auto_signup = auto_signup
        self.signup_password = signup_password or "RobloxGen2024!"
        self.created_accounts = []

    def run(self):
        for i, username in enumerate(self.usernames):
            if not self.running:
                break
            self.check_user(username)
            self.count += 1
            self.pupdate.emit(self.count)

    def stop(self):
        self.running = False

    def check_user(self, username):
        if not self.running:
            return

        try:
            url = "https://users.roblox.com/v1/usernames/users"
            data = {"usernames": [username]}
            
            if self.debug:
                self.update.emit(f"\n{'='*60}")
                self.update.emit(f"[DEBUG] Checking: {username}")
                self.update.emit(f"[DEBUG] API URL: {url}")
            
            response = requests.post(url, json=data, timeout=10)
            
            if self.debug:
                self.update.emit(f"[DEBUG] Status Code: {response.status_code}")
                self.update.emit(f"[DEBUG] Response: {response.text[:200]}")
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get("data") and len(result["data"]) > 0:
                    user_data = result["data"][0]
                    if user_data.get("id") is not None:
                        user_id = user_data.get("id")
                        display_name = user_data.get("displayName", username)
                        self.update.emit(f"❌ [TAKEN] {username} (ID: {user_id}, Display: {display_name})")
                        self.consecutive_errors = 0
                        return
                
                # Username is available
                self.update.emit(f"✅ [AVAILABLE] {username}")
                self.consecutive_errors = 0
                
                # Send to Discord webhook if provided
                if self.webhook_url:
                    self.send_to_discord(username)
                
                # Auto sign-up if enabled
                if self.auto_signup and DRISSION_AVAILABLE:
                    self.update.emit(f"🔄 [AUTO-SIGNUP] Attempting to create account: {username}")
                    success = self.create_account(username)
                    if success:
                        self.update.emit(f"🎉 [SUCCESS] Account created: {username}")
                    else:
                        self.update.emit(f"⚠️ [FAILED] Could not create account: {username}")
                
            elif response.status_code == 429:
                self.update.emit(f"⚠️ [RATE LIMIT] {username}: Slow down!")
                self.consecutive_errors += 1
                
            else:
                self.update.emit(f"⚠️ [ERROR] {username}: Status {response.status_code}")
                self.consecutive_errors += 1
                
        except requests.exceptions.Timeout:
            self.consecutive_errors += 1
            self.update.emit(f"⏱️ [TIMEOUT] {username}")
            
        except Exception as e:
            self.consecutive_errors += 1
            if self.debug:
                error_msg = traceback.format_exc()
                self.update.emit(f"⚠️ [ERROR] {username}:\n{error_msg}")
            else:
                error_msg = str(e)
                self.update.emit(f"⚠️ [ERROR] {username}: {error_msg}")

    def create_account(self, username):
        """Create a Roblox account using DrissionPage"""
        if not DRISSION_AVAILABLE:
            self.update.emit(f"[DEBUG] DrissionPage not available")
            return False
        
        chrome = None
        try:
            co = ChromiumOptions()
            co.set_argument("--lang", "en")
            co.auto_port().mute(True)
            # Browser must be visible to properly detect redirects
            
            if self.debug:
                self.update.emit(f"[DEBUG] Initializing browser for {username}")
            
            chrome = Chromium(addr_or_opts=co)
            page = chrome.latest_tab
            
            if self.debug:
                self.update.emit(f"[DEBUG] Navigating to signup page")
            
            # Navigate to signup page
            page.get("https://www.roblox.com/CreateAccount")
            
            # Accept cookies if present
            try:
                page.ele('@class=btn-cta-lg cookie-btn btn-primary-md btn-min-width', timeout=3).click()
                if self.debug:
                    self.update.emit(f"[DEBUG] Accepted cookies")
            except:
                if self.debug:
                    self.update.emit(f"[DEBUG] No cookie banner found")
            
            # Set birthday (random adult age)
            from datetime import datetime
            import locale
            import time
            
            if self.debug:
                self.update.emit(f"[DEBUG] Setting birthday")
            
            bdaymonthelement = page.ele("#MonthDropdown", timeout=10)
            oldLocale = locale.getlocale(locale.LC_TIME)
            try:
                locale.setlocale(locale.LC_TIME, 'C')
                currentMonth = datetime.now().strftime("%b")
            finally:
                try:
                    locale.setlocale(locale.LC_TIME, oldLocale)
                except:
                    pass
            
            bdaymonthelement.select.by_value(currentMonth)
            
            bdaydayelement = page.ele("#DayDropdown", timeout=10)
            currentDay = datetime.now().day
            if currentDay <= 9:
                bdaydayelement.select.by_value(f"0{currentDay}")
            else:
                bdaydayelement.select.by_value(str(currentDay))
            
            currentYear = datetime.now().year - 19
            page.ele("#YearDropdown", timeout=10).select.by_value(str(currentYear))
            
            if self.debug:
                self.update.emit(f"[DEBUG] Entering credentials")
            
            # Enter username and password
            page.ele("#signup-username", timeout=10).input(username)
            time.sleep(0.5)
            page.ele("#signup-password", timeout=10).input(self.signup_password)
            
            # Wait a moment
            time.sleep(2)
            
            # Accept terms
            try:
                checkbox = page.ele('@@id=signup-checkbox@@class=checkbox', timeout=3)
                checkbox.click()
                if self.debug:
                    self.update.emit(f"[DEBUG] Accepted terms checkbox")
            except:
                # Try alternative checkbox selector
                try:
                    checkbox = page.ele('#signup-checkbox', timeout=2)
                    checkbox.click()
                    if self.debug:
                        self.update.emit(f"[DEBUG] Accepted terms checkbox (alt method)")
                except:
                    if self.debug:
                        self.update.emit(f"[DEBUG] Terms checkbox not required or already checked")
                    pass
            
            time.sleep(1)
            
            if self.debug:
                self.update.emit(f"[DEBUG] Submitting signup form")
            
            # Submit signup
            page.ele("@@id=signup-button@@name=signupSubmit", timeout=10).click()
            
            # Wait longer and check for errors
            time.sleep(8)
            
            # Check for error messages
            try:
                error_element = page.ele(".text-error", timeout=2)
                if error_element:
                    error_text = error_element.text
                    self.update.emit(f"⚠️ [SIGNUP ERROR] {username}: {error_text}")
                    if chrome:
                        chrome.quit()
                    return False
            except:
                pass
            
            # Check current URL
            current_url = page.url
            if self.debug:
                self.update.emit(f"[DEBUG] Current URL: {current_url}")
            
            # Check if we're redirected to home (success)
            if "home" in current_url.lower() or "/home" in current_url:
                if self.debug:
                    self.update.emit(f"[DEBUG] Successfully created account, getting cookies")
                
                # Get cookies
                cookies = []
                for cookie in page.cookies():
                    cookies.append({
                        "name": cookie["name"],
                        "value": cookie["value"]
                    })
                
                # Save account
                account_data = {
                    "username": username,
                    "password": self.signup_password,
                    "cookies": cookies,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self.created_accounts.append(account_data)
                
                # Save to file
                self.save_account(account_data)
                
                if chrome:
                    chrome.quit()
                return True
            else:
                # Check if there's a captcha
                try:
                    captcha = page.get_frame('xpath://*[@id="arkose-iframe"]')
                    if captcha:
                        self.update.emit(f"⚠️ [CAPTCHA] {username}: Captcha detected, cannot auto-complete")
                except:
                    pass
                
                if self.debug:
                    self.update.emit(f"[DEBUG] Signup did not redirect to home page")
                
                if chrome:
                    chrome.quit()
                return False
                
        except Exception as e:
            error_msg = str(e)
            if self.debug:
                error_msg = traceback.format_exc()
            self.update.emit(f"⚠️ [SIGNUP ERROR] {username}: {error_msg}")
            try:
                if chrome:
                    chrome.quit()
            except:
                pass
            return False
    
    def save_account(self, account_data):
        """Save created account to files"""
        try:
            # Save to accounts.txt
            with open("auto_created_accounts.txt", "a", encoding="utf-8") as f:
                f.write(f"Username: {account_data['username']}, Password: {account_data['password']} (Created: {account_data['created_at']})\n")
            
            # Save to JSON with cookies
            try:
                with open("auto_created_cookies.json", "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except:
                existing = []
            
            existing.append(account_data)
            
            with open("auto_created_cookies.json", "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=4)
                
        except Exception as e:
            if self.debug:
                self.update.emit(f"[DEBUG] Error saving account: {str(e)}")

    def send_to_discord(self, username):
        """Send available username to Discord webhook"""
        try:
            webhook_data = {
                "embeds": [{
                    "title": "🎮 Available Roblox Username Found!",
                    "description": f"**Username:** `{username}`",
                    "color": 3447003,
                    "fields": [
                        {
                            "name": "🔗 Direct Link",
                            "value": f"https://www.roblox.com/search/users?keyword={username}",
                            "inline": False
                        }
                    ],
                    "footer": {
                        "text": "Roblox Username Checker"
                    }
                }]
            }
            
            response = requests.post(self.webhook_url, json=webhook_data, timeout=5)
            
            if response.status_code == 204:
                if self.debug:
                    self.update.emit(f"[DEBUG] ✅ Sent {username} to Discord webhook")
            else:
                if self.debug:
                    self.update.emit(f"[DEBUG] ⚠️ Webhook failed: Status {response.status_code}")
                    
        except Exception as e:
            if self.debug:
                self.update.emit(f"[DEBUG] ⚠️ Webhook error: {str(e)}")

# ------------------- GUI App ------------------- #
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Roblox Username Checker with Auto Sign-Up")
        self.setGeometry(150, 150, 1100, 850)
        self.thread = None
        self.initUI()

    def initUI(self):
        wid = QWidget(self)
        self.setCentralWidget(wid)
        main_layout = QVBoxLayout()
        wid.setLayout(main_layout)

        # Title
        title = QLabel("🎮 Roblox Username Checker + Auto Sign-Up")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("padding: 15px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #e74c3c, stop:1 #3498db); color: white; border-radius: 5px;")
        main_layout.addWidget(title)

        # Info Section
        info_group = QGroupBox("ℹ️ About This Tool")
        info_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        info_layout = QVBoxLayout()
        
        instruction = QLabel("✨ Check username availability using Roblox's public API\n🤖 NEW: Automatically create accounts for available usernames!\n⚠️ Note: Rate limits may apply. Auto sign-up requires DrissionPage library.")
        instruction.setWordWrap(True)
        instruction.setStyleSheet("background-color: #e7f3ff; padding: 10px; border-radius: 3px; color: #004085;")
        info_layout.addWidget(instruction)
        
        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)

        # Auto Sign-Up Section
        signup_group = QGroupBox("🤖 Auto Sign-Up Settings")
        signup_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        signup_layout = QVBoxLayout()
        
        checkbox_layout = QHBoxLayout()
        self.auto_signup_checkbox = QCheckBox("Enable Auto Sign-Up for Available Usernames")
        if not DRISSION_AVAILABLE:
            self.auto_signup_checkbox.setEnabled(False)
            self.auto_signup_checkbox.setToolTip("Install DrissionPage: pip install DrissionPage")
        checkbox_layout.addWidget(self.auto_signup_checkbox)
        
        if not DRISSION_AVAILABLE:
            install_btn = QPushButton("📥 Install DrissionPage")
            install_btn.setMaximumWidth(180)
            install_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 5px; font-weight: bold;")
            install_btn.clicked.connect(self.install_drissionpage)
            checkbox_layout.addWidget(install_btn)
        
        checkbox_layout.addStretch()
        signup_layout.addLayout(checkbox_layout)
        
        password_layout = QHBoxLayout()
        password_layout.addWidget(QLabel("Account Password:"))
        self.signup_password_input = QLineEdit("RobloxGen2024!")
        self.signup_password_input.setPlaceholderText("Password for created accounts")
        password_layout.addWidget(self.signup_password_input)
        signup_layout.addLayout(password_layout)
        
        if not DRISSION_AVAILABLE:
            warning = QLabel("⚠️ DrissionPage not installed. Click the button above to install it automatically, or run: pip install DrissionPage")
            warning.setStyleSheet("background-color: #fff3cd; padding: 8px; border-radius: 3px; color: #856404;")
            warning.setWordWrap(True)
            signup_layout.addWidget(warning)
        
        signup_group.setLayout(signup_layout)
        main_layout.addWidget(signup_group)

        # Webhook Section
        webhook_group = QGroupBox("🔔 Discord Webhook (Optional)")
        webhook_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        webhook_layout = QVBoxLayout()
        
        webhook_info = QLabel("💬 Get notified when available usernames are found!")
        webhook_info.setWordWrap(True)
        webhook_info.setStyleSheet("background-color: #f8d7da; padding: 8px; border-radius: 3px; color: #721c24;")
        webhook_layout.addWidget(webhook_info)
        
        webhook_input_layout = QHBoxLayout()
        self.webhook_input = QLineEdit()
        self.webhook_input.setPlaceholderText("https://discord.com/api/webhooks/...")
        webhook_input_layout.addWidget(self.webhook_input)
        
        test_webhook_btn = QPushButton("🧪 Test")
        test_webhook_btn.setMaximumWidth(80)
        test_webhook_btn.clicked.connect(self.test_webhook)
        webhook_input_layout.addWidget(test_webhook_btn)
        
        webhook_layout.addLayout(webhook_input_layout)
        webhook_group.setLayout(webhook_layout)
        main_layout.addWidget(webhook_group)

        # Generator Section
        gen_group = QGroupBox("Step 1: Generate Random Usernames (Optional)")
        gen_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        gen_layout = QVBoxLayout()
        
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Length:"))
        self.length_input = QLineEdit("5")
        self.length_input.setMaximumWidth(60)
        row1.addWidget(self.length_input)
        
        row1.addWidget(QLabel("Prefix:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText("e.g., Pro")
        self.prefix_input.setMaximumWidth(100)
        row1.addWidget(self.prefix_input)
        
        row1.addWidget(QLabel("Suffix:"))
        self.suffix_input = QLineEdit()
        self.suffix_input.setPlaceholderText("e.g., _YT")
        self.suffix_input.setMaximumWidth(100)
        row1.addWidget(self.suffix_input)
        
        row1.addWidget(QLabel("Count:"))
        self.count_input = QLineEdit("10")
        self.count_input.setMaximumWidth(60)
        row1.addWidget(self.count_input)
        
        row1.addStretch()
        gen_layout.addLayout(row1)
        
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Pattern:"))
        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems([
            "Letters only (abc)",
            "Letters + Numbers (a1b2)",
            "Numbers + Letters (12ab)",
            "Letters_Letters (abc_def)",
            "CamelCase (AbcDef)"
        ])
        self.pattern_combo.setMaximumWidth(200)
        row2.addWidget(self.pattern_combo)
        
        self.gen_button = QPushButton("🎲 Generate")
        self.gen_button.clicked.connect(self.generate_usernames)
        self.gen_button.setStyleSheet("background-color: #3498db; color: white; padding: 8px; font-weight: bold;")
        row2.addWidget(self.gen_button)
        
        self.debug_checkbox = QCheckBox("🛠 Debug Mode")
        self.debug_checkbox.setToolTip("Show detailed API responses")
        row2.addWidget(self.debug_checkbox)
        
        row2.addStretch()
        gen_layout.addLayout(row2)
        
        gen_group.setLayout(gen_layout)
        main_layout.addWidget(gen_group)

        # Input/Output Section
        io_group = QGroupBox("Step 2: Check Usernames")
        io_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        io_layout = QHBoxLayout()
        
        input_box = QVBoxLayout()
        input_label = QLabel("📝 Usernames to Check:")
        input_label.setStyleSheet("font-weight: bold;")
        input_box.addWidget(input_label)
        
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Enter usernames here\n(one per line)\n\nExample:\nCoolGamer123\nProPlayer\nEpicUser")
        input_box.addWidget(self.input_text)
        
        output_box = QVBoxLayout()
        output_label = QLabel("📊 Results:")
        output_label.setStyleSheet("font-weight: bold;")
        output_box.addWidget(output_label)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setStyleSheet("background-color: #1a1a1a; color: #00ff00; font-family: Consolas, Monaco, monospace; padding: 10px;")
        output_box.addWidget(self.output_text)
        
        io_layout.addLayout(input_box)
        io_layout.addLayout(output_box)
        io_group.setLayout(io_layout)
        main_layout.addWidget(io_group)

        # Control Buttons
        btn_layout = QHBoxLayout()
        
        self.start_button = QPushButton("▶️ START CHECKING")
        self.start_button.clicked.connect(self.start_clicked)
        self.start_button.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("⏹️ STOP")
        self.stop_button.clicked.connect(self.stop_clicked)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_layout.addWidget(self.stop_button)
        
        self.clear_button = QPushButton("🗑️ Clear Results")
        self.clear_button.clicked.connect(lambda: self.output_text.clear())
        self.clear_button.setStyleSheet("padding: 15px;")
        btn_layout.addWidget(self.clear_button)
        
        main_layout.addLayout(btn_layout)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("QProgressBar { text-align: center; height: 25px; } QProgressBar::chunk { background-color: #3498db; }")
        main_layout.addWidget(self.progress_bar)

        # Status Label
        self.status_label = QLabel("✅ Ready - No login required!")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #e0e0e0; border-radius: 3px;")
        main_layout.addWidget(self.status_label)

    def generate_usernames(self):
        try:
            length = int(self.length_input.text())
        except:
            length = 5
        
        prefix = self.prefix_input.text().strip()
        suffix = self.suffix_input.text().strip()
        pattern = self.pattern_combo.currentText()
        
        try:
            count = int(self.count_input.text())
        except:
            count = 10

        generated = []
        
        for _ in range(count):
            username = ""
            
            if pattern == "Letters only (abc)":
                username = "".join(random.choice(string.ascii_lowercase) for _ in range(length))
            
            elif pattern == "Letters + Numbers (a1b2)":
                chars = string.ascii_lowercase + string.digits
                username = "".join(random.choice(chars) for _ in range(length))
            
            elif pattern == "Numbers + Letters (12ab)":
                num_count = random.randint(1, max(1, length - 2))
                letter_count = length - num_count
                username = "".join(random.choice(string.digits) for _ in range(num_count))
                username += "".join(random.choice(string.ascii_lowercase) for _ in range(letter_count))
            
            elif pattern == "Letters_Letters (abc_def)":
                part1_len = length // 2
                part2_len = length - part1_len
                part1 = "".join(random.choice(string.ascii_lowercase) for _ in range(part1_len))
                part2 = "".join(random.choice(string.ascii_lowercase) for _ in range(part2_len))
                username = f"{part1}_{part2}"
            
            elif pattern == "CamelCase (AbcDef)":
                parts = []
                remaining = length
                while remaining > 0:
                    part_len = random.randint(2, min(4, remaining))
                    part = "".join(random.choice(string.ascii_lowercase) for _ in range(part_len))
                    part = part.capitalize()
                    parts.append(part)
                    remaining -= part_len
                username = "".join(parts)
            
            username = prefix + username + suffix
            username = ''.join(c for c in username if c.isalnum() or c == '_')
            
            if 3 <= len(username) <= 20:
                generated.append(username)

        existing = self.input_text.toPlainText().strip()
        all_users = ("\n".join(generated) if not existing else existing + "\n" + "\n".join(generated))
        self.input_text.setText(all_users)
        
        self.status_label.setText(f"✅ Generated {len(generated)} usernames")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")

    def test_webhook(self):
        webhook_url = self.webhook_input.text().strip()
        
        if not webhook_url:
            QMessageBox.warning(self, "No Webhook", "Please enter a webhook URL first!")
            return
        
        try:
            test_data = {
                "embeds": [{
                    "title": "🧪 Test Message",
                    "description": "Your webhook is working correctly!",
                    "color": 5763719,
                    "footer": {
                        "text": "Roblox Username Checker - Webhook Test"
                    }
                }]
            }
            
            response = requests.post(webhook_url, json=test_data, timeout=5)
            
            if response.status_code == 204:
                QMessageBox.information(self, "Success", "✅ Webhook test successful!\nCheck your Discord channel.")
            else:
                QMessageBox.warning(self, "Failed", f"❌ Webhook test failed!\nStatus code: {response.status_code}")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"❌ Failed to send test message:\n{str(e)}")

    def start_clicked(self):
        usernames = self.get_usernames()
        if not usernames:
            QMessageBox.warning(self, "No Usernames", "Please enter or generate usernames to check!")
            return
        
        debug = self.debug_checkbox.isChecked()
        webhook_url = self.webhook_input.text().strip() or None
        auto_signup = self.auto_signup_checkbox.isChecked()
        signup_password = self.signup_password_input.text().strip()
        
        if auto_signup and not DRISSION_AVAILABLE:
            QMessageBox.warning(self, "Library Missing", "DrissionPage is not installed!\nInstall it with: pip install DrissionPage")
            return
        
        self.progress_bar.setMaximum(len(usernames))
        self.progress_bar.setValue(0)
        self.output_text.clear()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        status_text = f"🔄 Checking {len(usernames)} usernames"
        if auto_signup:
            status_text += " (auto sign-up enabled)"
        if webhook_url:
            status_text += " (webhook enabled)"
        status_text += "..."
        
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #fff9c4; border-radius: 3px;")

        self.thread = Checker(usernames, webhook_url, debug, auto_signup, signup_password)
        self.thread.update.connect(self.update_text)
        self.thread.pupdate.connect(self.update_progress)
        self.thread.finished.connect(self.checking_finished)
        self.thread.start()

    def stop_clicked(self):
        if self.thread:
            self.thread.stop()
            self.thread.quit()
            self.thread.wait(2000)
        self.checking_finished()

    def checking_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        
        if self.thread and len(self.thread.created_accounts) > 0:
            self.status_label.setText(f"✅ Complete! Created {len(self.thread.created_accounts)} accounts")
            QMessageBox.information(self, "Success", 
                f"🎉 Successfully created {len(self.thread.created_accounts)} accounts!\n\n"
                f"Credentials saved to:\n"
                f"• auto_created_accounts.txt\n"
                f"• auto_created_cookies.json")
        else:
            self.status_label.setText("✅ Checking complete!")
        
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")

    def update_text(self, text):
        self.output_text.append(text)
        cursor = self.output_text.textCursor()
        cursor.movePosition(cursor.End)
        self.output_text.setTextCursor(cursor)

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        total = self.progress_bar.maximum()
        percent = int((value / total) * 100) if total > 0 else 0
        self.status_label.setText(f"🔄 Progress: {value}/{total} ({percent}%)")

    def get_usernames(self):
        txt = self.input_text.toPlainText().strip()
        usernames = []
        for line in txt.splitlines():
            u = line.strip()
            if u and (u.replace('_', '').isalnum()):
                usernames.append(u)
        return usernames
    
    def install_drissionpage(self):
        """Install DrissionPage library"""
        reply = QMessageBox.question(self, 'Install DrissionPage', 
            "This will install DrissionPage library using pip.\n\n"
            "After installation, you'll need to restart the application.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            import subprocess
            try:
                self.status_label.setText("📥 Installing DrissionPage...")
                self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #fff9c4; border-radius: 3px;")
                QApplication.processEvents()  # Update UI
                
                result = subprocess.run([sys.executable, "-m", "pip", "install", "DrissionPage"], 
                                      capture_output=True, text=True)
                
                if result.returncode == 0:
                    QMessageBox.information(self, "Success", 
                        "✅ DrissionPage installed successfully!\n\n"
                        "Please restart the application to use Auto Sign-Up feature.")
                    self.status_label.setText("✅ Installation complete - Please restart app")
                    self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")
                else:
                    QMessageBox.warning(self, "Installation Failed", 
                        f"❌ Failed to install DrissionPage.\n\n"
                        f"Error: {result.stderr}\n\n"
                        f"Try manually: pip install DrissionPage")
                    self.status_label.setText("❌ Installation failed")
                    self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #f8d7da; border-radius: 3px;")
            except Exception as e:
                QMessageBox.critical(self, "Error", 
                    f"❌ An error occurred:\n{str(e)}\n\n"
                    f"Try manually: pip install DrissionPage")
                self.status_label.setText("❌ Installation error")
                self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #f8d7da; border-radius: 3px;")

# ------------------- Run ------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())
