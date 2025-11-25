import sys, aiohttp, asyncio, random, string, re, json
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import QFont

# ------------------- Checker Thread ------------------- #
class Checker(QThread):
    update = pyqtSignal(str)
    pupdate = pyqtSignal(int)
    count = 0

    BASE_URL = "https://www.instagram.com/{}/"

    def __init__(self, usernames, sessionid, user_agent, debug=False):
        super().__init__()
        self.usernames = usernames
        self.sessionid = sessionid
        self.user_agent = user_agent
        self.running = True
        self.debug = debug
        self.consecutive_errors = 0  # Track errors in a row
        self.rate_limit_count = 0  # Track rate limits
        self.max_errors_before_pause = 3  # Pause after 3 errors in a row

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.main())
        finally:
            loop.close()

    def stop(self):
        self.running = False

    async def check_user(self, username, sem, session, lock, idx):
        if not self.running:
            return

        async with sem:
            try:
                url = self.BASE_URL.format(username)
                
                async with session.get(url, allow_redirects=True, timeout=20) as resp:
                    status = resp.status
                    
                    # Read the response
                    try:
                        body = await resp.text(errors='ignore')
                        body_lower = body.lower()
                    except Exception as e:
                        self.update.emit(f"⚠️ [ERROR] {username}: Could not read response")
                        return
                    
                    # Debug mode - show raw indicators
                    if self.debug:
                        self.update.emit(f"\n{'='*60}")
                        self.update.emit(f"[DEBUG] Checking: {username}")
                        self.update.emit(f"[DEBUG] Status Code: {status}")
                        self.update.emit(f"[DEBUG] Final URL: {resp.url}")
                        self.update.emit(f"[DEBUG] Body Length: {len(body)} chars")
                    
                    # ===== CLEAR SIGNALS =====
                    
                    # 1. Explicit 404 status = AVAILABLE
                    if status == 404:
                        self.update.emit(f"✅ [AVAILABLE] {username} (404 status)")
                        return
                    
                    # 2. Rate limited
                    if status == 429:
                        self.update.emit(f"⚠️ [RATE LIMIT] {username}: Slow down!")
                        await asyncio.sleep(5)
                        return
                    
                    # 3. Blocked or forbidden
                    if status in [400, 403]:
                        self.consecutive_errors += 1
                        self.update.emit(f"⚠️ [BLOCKED] {username}: Status {status} - Check session/IP")
                        await self.check_for_cooldown()
                        return
                    
                    # 4. Redirected to login = session expired
                    if 'login' in str(resp.url).lower():
                        self.update.emit(f"❌ [SESSION EXPIRED] {username}: Re-enter sessionid")
                        return
                    
                    # ===== ANALYZE BODY CONTENT =====
                    
                    # Check for explicit "page not found" signals
                    not_found_signals = [
                        '"HttpError":{"statusCode":404',  # JSON error object
                        'page_not_found',  # Page type
                        '"PageNotFound"',  # React component
                        'Sorry, this page isn\'t available',  # Error message
                        '"status_code":404'  # Alternative error format
                    ]
                    
                    found_not_found = False
                    for signal in not_found_signals:
                        if signal.lower() in body_lower:
                            if self.debug:
                                self.update.emit(f"[DEBUG] Found NOT FOUND signal: {signal}")
                            found_not_found = True
                            break
                    
                    if found_not_found:
                        self.update.emit(f"✅ [AVAILABLE] {username} (not found signal)")
                        return
                    
                    # Check for profile existence signals
                    # These indicate a REAL, ACTIVE profile (not just placeholder data)
                    profile_signals = {
                        'has_real_user_id': False,
                        'has_follower_count': False,
                        'has_following_count': False,
                        'has_post_count': False,
                        'has_profile_pic': False,
                        'has_biography_content': False,
                        'has_username_match': False
                    }
                    
                    # User ID check - but verify it's actually in a user object, not just random
                    # Real profiles have user data in specific structures
                    user_id_match = re.search(r'"user"[:\s]*{[^}]*"id"[:\s]*"(\d{5,})"', body)
                    if not user_id_match:
                        user_id_match = re.search(r'"ProfilePage"[^}]*"user"[:\s]*{[^}]*"id"[:\s]*"(\d{5,})"', body, re.DOTALL)
                    
                    if user_id_match:
                        user_id = user_id_match.group(1)
                        # Check if this user ID appears with the username (strong signal)
                        if re.search(rf'"username"[:\s]*"{username}"[^}}]*"id"[:\s]*"{user_id}"', body, re.IGNORECASE):
                            profile_signals['has_real_user_id'] = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Found REAL user ID linked to username: {user_id}")
                        elif self.debug:
                            self.update.emit(f"[DEBUG] ✗ Found user ID {user_id} but NOT linked to this username (likely placeholder)")
                    
                    # Username appears in the user data (strong signal it's real)
                    if re.search(rf'"username"[:\s]*"{username}"', body, re.IGNORECASE):
                        profile_signals['has_username_match'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Username '{username}' found in user data")
                    
                    # Follower count structure
                    if re.search(r'"edge_followed_by"[:\s]*{[^}]*"count"[:\s]*\d+', body):
                        profile_signals['has_follower_count'] = True
                        if self.debug:
                            match = re.search(r'"edge_followed_by"[:\s]*{[^}]*"count"[:\s]*(\d+)', body)
                            if match:
                                self.update.emit(f"[DEBUG] ✓ Found follower count: {match.group(1)}")
                    
                    # Following count structure
                    if re.search(r'"edge_follow"[:\s]*{[^}]*"count"[:\s]*\d+', body):
                        profile_signals['has_following_count'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Found following count")
                    
                    # Post count
                    if re.search(r'"edge_owner_to_timeline_media"[:\s]*{[^}]*"count"[:\s]*\d+', body):
                        profile_signals['has_post_count'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Found post count")
                    
                    # Profile picture with actual URL (not default)
                    if re.search(r'"profile_pic_url"[:\s]*"https://[^"]+scontent[^"]*"', body):
                        profile_signals['has_profile_pic'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Found profile pic URL")
                    
                    # Biography with actual content (not empty string)
                    bio_match = re.search(r'"biography"[:\s]*"([^"]+)"', body)
                    if bio_match and bio_match.group(1).strip():
                        profile_signals['has_biography_content'] = True
                        if self.debug:
                            bio_preview = bio_match.group(1)[:50]
                            self.update.emit(f"[DEBUG] ✓ Found biography with content: {bio_preview}...")
                    elif self.debug:
                        self.update.emit(f"[DEBUG] ✗ Biography field empty or not found")
                    
                    # Count how many profile signals we found
                    signal_count = sum(profile_signals.values())
                    
                    if self.debug:
                        self.update.emit(f"[DEBUG] Profile signals found: {signal_count}/7")
                        self.update.emit(f"[DEBUG] Signals: {profile_signals}")
                    
                    # Decision logic - STRICTER:
                    # Must have username match + real user ID to be considered taken
                    # OR have multiple strong signals (follower counts, posts, pic)
                    
                    if profile_signals['has_username_match'] and profile_signals['has_real_user_id']:
                        self.update.emit(f"❌ [TAKEN] {username} (username + user_id confirmed)")
                        return
                    
                    if signal_count >= 4:
                        self.update.emit(f"❌ [TAKEN] {username} ({signal_count} strong signals)")
                        return
                    
                    # Has follower/following/post counts = likely real
                    engagement_signals = (
                        profile_signals['has_follower_count'] + 
                        profile_signals['has_following_count'] + 
                        profile_signals['has_post_count']
                    )
                    if engagement_signals >= 2 and profile_signals['has_profile_pic']:
                        self.update.emit(f"❌ [TAKEN] {username} (engagement data present)")
                        return
                    
                    # Additional check: Look for the username in the page title or meta
                    username_in_meta = False
                    title_match = re.search(r'<title>([^<]+)</title>', body, re.IGNORECASE)
                    if title_match:
                        title = title_match.group(1)
                        # Check if it's a real profile title (has @username or posts/followers)
                        if username.lower() in title.lower() and ('posts' in title.lower() or 'followers' in title.lower() or f'@{username}' in title.lower()):
                            username_in_meta = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Username found in profile title: {title}")
                        elif self.debug:
                            self.update.emit(f"[DEBUG] ✗ Title doesn't indicate real profile: {title}")
                    
                    if username_in_meta and signal_count >= 2:
                        self.update.emit(f"❌ [TAKEN] {username} (profile title + {signal_count} signals)")
                        return
                    
                    # If we get here with very few signals, it's likely available
                    if signal_count <= 1:
                        self.consecutive_errors = 0  # Reset on success
                        self.update.emit(f"✅ [AVAILABLE] {username} (no real profile data)")
                        return
                    
                    # Low signal count = probably available (just has placeholder data)
                    if signal_count == 2 and not profile_signals['has_username_match']:
                        self.consecutive_errors = 0  # Reset on success
                        self.update.emit(f"✅ [AVAILABLE] {username} (only placeholder data)")
                        return
                    
                    # Edge case: Some signals but unclear
                    self.consecutive_errors = 0  # Reset on success
                    self.update.emit(f"❓ [UNCLEAR] {username} ({signal_count} signals - manual check recommended)")
                    if self.debug:
                        self.update.emit(f"[DEBUG] URL for manual check: {url}")
                    
            except asyncio.TimeoutError:
                self.consecutive_errors += 1
                self.update.emit(f"⏱️ [TIMEOUT] {username}")
                await self.check_for_cooldown()
            except Exception as e:
                self.consecutive_errors += 1
                error_msg = str(e)[:80]
                self.update.emit(f"⚠️ [ERROR] {username}: {error_msg}")
                await self.check_for_cooldown()
            finally:
                async with lock:
                    self.count += 1
                self.pupdate.emit(self.count)

    async def check_for_cooldown(self):
        """Check if we need to pause due to consecutive errors"""
        if self.consecutive_errors >= self.max_errors_before_pause:
            await self.cooldown(15, f"{self.consecutive_errors} errors in a row")
            self.consecutive_errors = 0  # Reset counter after cooldown

    async def cooldown(self, duration, reason):
        """Pause checking for a specified duration"""
        self.update.emit(f"\n🛑 COOLDOWN: {reason}!")
        self.update.emit(f"⏸️  Pausing for {duration} seconds to avoid being blocked...")
        
        for remaining in range(duration, 0, -1):
            if not self.running:  # Allow user to stop during cooldown
                break
            self.update.emit(f"⏳ Resuming in {remaining} seconds...")
            await asyncio.sleep(1)
        
        self.update.emit(f"✅ Cooldown complete! Continuing...\n")

    async def main(self):
        sem = asyncio.Semaphore(2)  # Max 2 concurrent requests
        lock = asyncio.Lock()

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",  # Removed 'br' to avoid brotli requirement
            "Connection": "keep-alive",
            "Cookie": f"sessionid={self.sessionid}",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none"
        }

        connector = aiohttp.TCPConnector(limit=2, ssl=True)
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
            for i, username in enumerate(self.usernames):
                if not self.running:
                    break
                await self.check_user(username, sem, session, lock, i)
                await asyncio.sleep(2)  # Increased delay to avoid rate limits

# ------------------- GUI App ------------------- #
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instagram Username Checker - Improved")
        self.setGeometry(150, 150, 1100, 800)
        self.thread = None
        self.initUI()

    def initUI(self):
        wid = QWidget(self)
        self.setCentralWidget(wid)
        main_layout = QVBoxLayout()
        wid.setLayout(main_layout)

        # Title
        title = QLabel("🔍 Instagram Username Checker - Improved Detection")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("padding: 15px; background-color: #e91e63; color: white; border-radius: 5px;")
        main_layout.addWidget(title)

        # SessionID Section
        sessionid_group = QGroupBox("Step 1: Enter Your Instagram sessionid")
        sessionid_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        sessionid_layout = QVBoxLayout()
        
        instruction = QLabel("📌 Get sessionid: Press F12 → Application/Storage → Cookies → instagram.com → copy 'sessionid' value")
        instruction.setWordWrap(True)
        instruction.setStyleSheet("background-color: #fff3cd; padding: 8px; border-radius: 3px; color: #856404;")
        sessionid_layout.addWidget(instruction)
        
        sessionid_input_layout = QHBoxLayout()
        self.sessionid_input = QLineEdit()
        self.sessionid_input.setPlaceholderText("Paste sessionid here...")
        self.sessionid_input.setEchoMode(QLineEdit.Password)
        sessionid_input_layout.addWidget(self.sessionid_input)
        
        show_btn = QPushButton("👁️")
        show_btn.setMaximumWidth(40)
        show_btn.clicked.connect(self.toggle_visibility)
        sessionid_input_layout.addWidget(show_btn)
        sessionid_layout.addLayout(sessionid_input_layout)
        
        sessionid_group.setLayout(sessionid_layout)
        main_layout.addWidget(sessionid_group)

        # Generator Section
        gen_group = QGroupBox("Step 2: Generate Random Usernames (Optional)")
        gen_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        gen_layout = QVBoxLayout()
        
        # First row - basic options
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Length:"))
        self.length_input = QLineEdit("5")
        self.length_input.setMaximumWidth(60)
        row1.addWidget(self.length_input)
        
        row1.addWidget(QLabel("Prefix:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText("e.g., og")
        self.prefix_input.setMaximumWidth(100)
        row1.addWidget(self.prefix_input)
        
        row1.addWidget(QLabel("Suffix:"))
        self.suffix_input = QLineEdit()
        self.suffix_input.setPlaceholderText("e.g., .og")
        self.suffix_input.setMaximumWidth(100)
        row1.addWidget(self.suffix_input)
        
        row1.addWidget(QLabel("Count:"))
        self.count_input = QLineEdit("10")
        self.count_input.setMaximumWidth(60)
        row1.addWidget(self.count_input)
        
        row1.addStretch()
        gen_layout.addLayout(row1)
        
        # Second row - pattern options
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Pattern:"))
        self.pattern_combo = QComboBox()
        self.pattern_combo.addItems([
            "Letters only (abc)",
            "Letters + Numbers (a1b2)",
            "Numbers + Letters (12ab)",
            "Letters_Letters (abc_def)",
            "Prefix_Letters (og_abc)",
            "Letters_Suffix (abc_og)"
        ])
        self.pattern_combo.setMaximumWidth(200)
        row2.addWidget(self.pattern_combo)
        
        self.gen_button = QPushButton("🎲 Generate")
        self.gen_button.clicked.connect(self.generate_usernames)
        self.gen_button.setStyleSheet("background-color: #2196F3; color: white; padding: 8px; font-weight: bold;")
        row2.addWidget(self.gen_button)
        
        self.debug_checkbox = QCheckBox("🐛 Debug Mode (Detailed)")
        self.debug_checkbox.setToolTip("Show detailed analysis of each username")
        row2.addWidget(self.debug_checkbox)
        
        row2.addStretch()
        gen_layout.addLayout(row2)
        
        gen_group.setLayout(gen_layout)
        main_layout.addWidget(gen_group)

        # Input/Output Section
        io_group = QGroupBox("Step 3: Check Usernames")
        io_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        io_layout = QHBoxLayout()
        
        # Input side
        input_box = QVBoxLayout()
        input_label = QLabel("📝 Usernames to Check:")
        input_label.setStyleSheet("font-weight: bold;")
        input_box.addWidget(input_label)
        
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Enter usernames here\n(one per line)\n\nExample:\ncoolname\nawesomeguy\ntestuser123")
        input_box.addWidget(self.input_text)
        
        # Output side
        output_box = QVBoxLayout()
        output_label = QLabel("📊 Results:")
        output_label.setStyleSheet("font-weight: bold;")
        output_box.addWidget(output_label)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setStyleSheet("background-color: #2b2b2b; color: #ffffff; font-family: Consolas, Monaco, monospace; padding: 10px;")
        output_box.addWidget(self.output_text)
        
        io_layout.addLayout(input_box)
        io_layout.addLayout(output_box)
        io_group.setLayout(io_layout)
        main_layout.addWidget(io_group)

        # Control Buttons
        btn_layout = QHBoxLayout()
        
        self.start_button = QPushButton("▶️ START CHECKING")
        self.start_button.clicked.connect(self.start_clicked)
        self.start_button.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("⏹️ STOP")
        self.stop_button.clicked.connect(self.stop_clicked)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_layout.addWidget(self.stop_button)
        
        self.clear_button = QPushButton("🗑️ Clear Results")
        self.clear_button.clicked.connect(lambda: self.output_text.clear())
        self.clear_button.setStyleSheet("padding: 15px;")
        btn_layout.addWidget(self.clear_button)
        
        main_layout.addLayout(btn_layout)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("QProgressBar { text-align: center; height: 25px; }")
        main_layout.addWidget(self.progress_bar)

        # Status Label
        self.status_label = QLabel("✅ Ready - Enable Debug Mode to see detailed analysis")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #e0e0e0; border-radius: 3px;")
        main_layout.addWidget(self.status_label)

    def toggle_visibility(self):
        if self.sessionid_input.echoMode() == QLineEdit.Password:
            self.sessionid_input.setEchoMode(QLineEdit.Normal)
        else:
            self.sessionid_input.setEchoMode(QLineEdit.Password)

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
                # Just random letters
                username = "".join(random.choice(string.ascii_lowercase) for _ in range(length))
            
            elif pattern == "Letters + Numbers (a1b2)":
                # Mix of letters and numbers
                chars = string.ascii_lowercase + string.digits
                username = "".join(random.choice(chars) for _ in range(length))
            
            elif pattern == "Numbers + Letters (12ab)":
                # Start with numbers, then letters
                num_count = random.randint(1, max(1, length - 2))
                letter_count = length - num_count
                username = "".join(random.choice(string.digits) for _ in range(num_count))
                username += "".join(random.choice(string.ascii_lowercase) for _ in range(letter_count))
            
            elif pattern == "Letters_Letters (abc_def)":
                # Two parts separated by underscore
                part1_len = length // 2
                part2_len = length - part1_len
                part1 = "".join(random.choice(string.ascii_lowercase) for _ in range(part1_len))
                part2 = "".join(random.choice(string.ascii_lowercase) for _ in range(part2_len))
                username = f"{part1}_{part2}"
            
            elif pattern == "Prefix_Letters (og_abc)":
                # Use prefix field + underscore + random letters
                if prefix:
                    letters = "".join(random.choice(string.ascii_lowercase) for _ in range(length))
                    username = f"{prefix}_{letters}"
                else:
                    # Fallback if no prefix
                    username = "".join(random.choice(string.ascii_lowercase) for _ in range(length))
            
            elif pattern == "Letters_Suffix (abc_og)":
                # Random letters + underscore + suffix field
                if suffix:
                    letters = "".join(random.choice(string.ascii_lowercase) for _ in range(length))
                    username = f"{letters}_{suffix}"
                else:
                    # Fallback if no suffix
                    username = "".join(random.choice(string.ascii_lowercase) for _ in range(length))
            
            # Add prefix and suffix if provided (for non-pattern modes)
            if pattern in ["Letters only (abc)", "Letters + Numbers (a1b2)", "Numbers + Letters (12ab)"]:
                username = prefix + username + suffix
            
            # Instagram rule: Cannot end with underscore or dot
            # Fix by replacing trailing _ or . with a random letter
            while username and username[-1] in ['_', '.']:
                username = username[:-1] + random.choice(string.ascii_lowercase)
            
            # Make sure it's valid
            if username and username.replace('_', '').replace('.', '').isalnum():
                generated.append(username)

        existing = self.input_text.toPlainText().strip()
        all_users = ("\n".join(generated) if not existing else existing + "\n" + "\n".join(generated))
        self.input_text.setText(all_users)
        
        self.status_label.setText(f"✅ Generated {len(generated)} usernames")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")

    def start_clicked(self):
        sessionid = self.sessionid_input.text().strip()
        if not sessionid:
            QMessageBox.warning(self, "Missing sessionid", "Please enter your Instagram sessionid first!")
            return
        
        usernames = self.get_usernames()
        if not usernames:
            QMessageBox.warning(self, "No Usernames", "Please enter or generate usernames to check!")
            return
        
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        debug = self.debug_checkbox.isChecked()
        
        self.progress_bar.setMaximum(len(usernames))
        self.progress_bar.setValue(0)
        self.output_text.clear()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText(f"🔄 Checking {len(usernames)} usernames...")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #fff9c4; border-radius: 3px;")

        self.thread = Checker(usernames, sessionid, ua, debug)
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
            u = line.strip().lower()
            if u.startswith('@'):
                u = u[1:]
            if u and u.replace('_', '').replace('.', '').isalnum():
                usernames.append(u)
        return usernames

# ------------------- Run ------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())