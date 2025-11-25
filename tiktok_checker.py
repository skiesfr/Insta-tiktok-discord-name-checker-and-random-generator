import sys, aiohttp, asyncio, random, string, re, json
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import QFont

# ------------------- Checker Thread ------------------- #
class Checker(QThread):
    update = pyqtSignal(str)
    pupdate = pyqtSignal(int)
    count = 0

    BASE_URL = "https://tiktok.com/@{}"

    def __init__(self, usernames, user_agent, debug=False):
        super().__init__()
        self.usernames = usernames
        self.user_agent = user_agent
        self.running = True
        self.debug = debug
        self.consecutive_errors = 0  # Track errors in a row
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
            retries = 2  # Try up to 2 times
            for attempt in range(retries):
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
                    
                    # 1. Rate limited
                    if status == 429:
                        self.update.emit(f"⚠️ [RATE LIMIT] {username}: Slow down!")
                        await asyncio.sleep(10)
                        return
                    
                    # 2. Blocked or forbidden
                    if status in [403]:
                        self.update.emit(f"⚠️ [BLOCKED] {username}: Status {status} - Try VPN or wait")
                        return
                    
                    # 3. Check if redirected (TikTok redirects invalid usernames)
                    final_url = str(resp.url).lower()
                    if username.lower() not in final_url:
                        if self.debug:
                            self.update.emit(f"[DEBUG] Redirected away from username - likely available")
                        self.update.emit(f"✅ [AVAILABLE] {username} (redirected)")
                        return
                    
                    # ===== ANALYZE BODY CONTENT =====
                    
                    # Check for explicit "not found" signals - but DON'T trust them yet
                    not_found_signals = [
                        "couldn't find this account",
                        "user not found",
                        "page not found",
                        "this account cannot be found",
                        '"statusCode":10202',  # TikTok error code for user not found
                        '"statusCode":10221',  # Another not found code
                    ]
                    
                    found_not_found = False
                    for signal in not_found_signals:
                        if signal.lower() in body_lower:
                            if self.debug:
                                self.update.emit(f"[DEBUG] Found NOT FOUND signal: {signal}")
                            found_not_found = True
                            break
                    
                    # DON'T return yet - check for other signals first
                    # TikTok shows "couldn't find" for private/banned accounts too!
                    
                    # Check for profile existence signals
                    profile_signals = {
                        'has_user_id': False,
                        'has_follower_count': False,
                        'has_following_count': False,
                        'has_video_count': False,
                        'has_verified_badge': False,
                        'has_signature': False,
                        'has_avatar': False,
                        'has_username_in_data': False,
                        'has_seo_data': False,
                        'has_private_account': False
                    }
                    
                    # Look for user ID in TikTok's data structure
                    user_id_patterns = [
                        r'"id"[:\s]*"(\d{10,})"',
                        r'"userId"[:\s]*"(\d{10,})"',
                        r'"uid"[:\s]*"(\d{10,})"',
                        r'"uniqueId"[:\s]*"' + re.escape(username) + r'"[^}]*"id"[:\s]*"(\d{10,})"'
                    ]
                    
                    for pattern in user_id_patterns:
                        user_id_match = re.search(pattern, body, re.IGNORECASE)
                        if user_id_match:
                            profile_signals['has_user_id'] = True
                            if self.debug:
                                try:
                                    user_id = user_id_match.group(1)
                                    self.update.emit(f"[DEBUG] ✓ Found user ID: {user_id}")
                                except:
                                    self.update.emit(f"[DEBUG] ✓ Found user ID pattern")
                            break
                    
                    # Check for username in data (strong signal)
                    if re.search(rf'"uniqueId"[:\s]*"{username}"', body, re.IGNORECASE):
                        profile_signals['has_username_in_data'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Username '{username}' found in user data")
                    
                    # Follower count
                    follower_patterns = [
                        r'"followerCount"[:\s]*(\d+)',
                        r'"fans"[:\s]*(\d+)',
                        r'<strong[^>]*data-e2e="followers-count"[^>]*>([0-9.KMB]+)</strong>'
                    ]
                    for pattern in follower_patterns:
                        if re.search(pattern, body):
                            profile_signals['has_follower_count'] = True
                            if self.debug:
                                match = re.search(pattern, body)
                                self.update.emit(f"[DEBUG] ✓ Found follower count: {match.group(1)}")
                            break
                    
                    # Following count
                    following_patterns = [
                        r'"followingCount"[:\s]*(\d+)',
                        r'"following"[:\s]*(\d+)',
                    ]
                    for pattern in following_patterns:
                        if re.search(pattern, body):
                            profile_signals['has_following_count'] = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Found following count")
                            break
                    
                    # Video count
                    video_patterns = [
                        r'"videoCount"[:\s]*(\d+)',
                        r'"video"[:\s]*(\d+)',
                    ]
                    for pattern in video_patterns:
                        if re.search(pattern, body):
                            profile_signals['has_video_count'] = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Found video count")
                            break
                    
                    # Verified badge
                    if re.search(r'"verified"[:\s]*true', body, re.IGNORECASE):
                        profile_signals['has_verified_badge'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Account is verified")
                    
                    # Signature/bio
                    if re.search(r'"signature"[:\s]*"[^"]+"', body):
                        profile_signals['has_signature'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Found signature/bio")
                    
                    # Avatar URL
                    avatar_patterns = [
                        r'"avatarLarger"[:\s]*"https://[^"]+"',
                        r'"avatarThumb"[:\s]*"https://[^"]+"',
                    ]
                    for pattern in avatar_patterns:
                        if re.search(pattern, body):
                            profile_signals['has_avatar'] = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Found avatar URL")
                            break
                    
                    # Check for SEO/meta data (TikTok includes this even for private accounts)
                    if re.search(rf'<meta[^>]*property="og:url"[^>]*content="[^"]*@{username}[^"]*"', body, re.IGNORECASE):
                        profile_signals['has_seo_data'] = True
                        if self.debug:
                            self.update.emit(f"[DEBUG] ✓ Found OpenGraph data with username")
                    
                    # Check page title for username (strong signal account exists)
                    title_match = re.search(r'<title>([^<]+)</title>', body, re.IGNORECASE)
                    if title_match:
                        title = title_match.group(1)
                        # If title contains the actual username (not just "TikTok"), account exists
                        if username.lower() in title.lower() and title.lower() != 'tiktok':
                            profile_signals['has_seo_data'] = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Username in title: {title}")
                    
                    # Check for private account indicator - BUT BE CAREFUL
                    # TikTok shows "This account is private" for both:
                    # 1. Actually private accounts (with user data)
                    # 2. Non-existent usernames (no user data)
                    # So we need OTHER signals to confirm it's real
                    if 'private account' in body_lower or '"privateAccount":true' in body_lower or re.search(r'this account is private', body, re.IGNORECASE):
                        # Only mark as private if we have OTHER evidence the account exists
                        if profile_signals['has_user_id'] or profile_signals['has_username_in_data'] or profile_signals['has_follower_count']:
                            profile_signals['has_private_account'] = True
                            if self.debug:
                                self.update.emit(f"[DEBUG] ✓ Account is PRIVATE (exists but hidden)")
                        elif self.debug:
                            self.update.emit(f"[DEBUG] ✗ Shows 'private' text but NO user data (generic error message)")
                    
                    # Count signals
                    signal_count = sum(profile_signals.values())
                    
                    if self.debug:
                        self.update.emit(f"[DEBUG] Profile signals found: {signal_count}/10")
                        self.update.emit(f"[DEBUG] Signals: {profile_signals}")
                        self.update.emit(f"[DEBUG] 'Not found' message present: {found_not_found}")
                    
                    # ===== DECISION LOGIC =====
                    
                    # PRIORITY 1: Check for REAL user data (strongest signals)
                    # If we have user_id + username match + follower count = definitely TAKEN
                    if profile_signals['has_user_id'] and profile_signals['has_username_in_data'] and profile_signals['has_follower_count']:
                        status = "private account" if profile_signals['has_private_account'] else "public account"
                        self.update.emit(f"❌ [TAKEN] {username} ({status} with confirmed data)")
                        return
                    
                    # If account is explicitly private WITH user data, it's TAKEN
                    if profile_signals['has_private_account'] and (profile_signals['has_user_id'] or profile_signals['has_follower_count']):
                        self.update.emit(f"❌ [TAKEN] {username} (private account - exists but hidden)")
                        return
                    
                    # If we have SEO data (title/meta tags) + other signals, account EXISTS
                    if profile_signals['has_seo_data'] and signal_count >= 2:
                        self.update.emit(f"❌ [TAKEN] {username} (SEO data + profile signals)")
                        return
                    
                    # Strong evidence of real profile
                    if profile_signals['has_username_in_data'] and profile_signals['has_user_id']:
                        self.update.emit(f"❌ [TAKEN] {username} (username + user_id confirmed)")
                        return
                    
                    # Multiple strong signals (4+)
                    if signal_count >= 4:
                        self.update.emit(f"❌ [TAKEN] {username} ({signal_count} strong signals)")
                        return
                    
                    # Has engagement metrics (followers/following/videos)
                    engagement_signals = (
                        profile_signals['has_follower_count'] + 
                        profile_signals['has_following_count'] + 
                        profile_signals['has_video_count']
                    )
                    if engagement_signals >= 2:
                        self.update.emit(f"❌ [TAKEN] {username} (engagement data present)")
                        return
                    
                    # PRIORITY 2: Check "not found" signal
                    # Only trust it if we have NO real user data
                    if found_not_found and signal_count == 0:
                        self.update.emit(f"✅ [AVAILABLE] {username} (not found + no profile data)")
                        return
                    
                    # "Not found" but only has "private" flag without real data = AVAILABLE
                    if found_not_found and signal_count == 1 and profile_signals['has_private_account']:
                        self.update.emit(f"✅ [AVAILABLE] {username} (generic error message, no real data)")
                        return
                    
                    # Found "not found" BUT has real signals = likely private/restricted
                    if found_not_found and signal_count > 1:
                        self.update.emit(f"❌ [TAKEN] {username} (shows 'not found' but has {signal_count} real signals)")
                        return
                    
                    # Check page title
                    title_match = re.search(r'<title>([^<]+)</title>', body, re.IGNORECASE)
                    if title_match:
                        title = title_match.group(1)
                        # Real profiles have username in title with @ or TikTok
                        if (f'@{username}' in title.lower() or username in title.lower()) and 'tiktok' in title.lower():
                            if signal_count >= 1:  # Even 1 signal + title = taken
                                if self.debug:
                                    self.update.emit(f"[DEBUG] ✓ Username confirmed in title: {title}")
                                self.update.emit(f"❌ [TAKEN] {username} (title confirms + {signal_count} signals)")
                                return
                    
                    # Low signal count = likely available
                    if signal_count <= 1:
                        self.update.emit(f"✅ [AVAILABLE] {username} (no real profile data)")
                        return
                    
                    # 2-3 signals but no strong confirmation
                    if signal_count <= 3 and not profile_signals['has_username_in_data']:
                        self.update.emit(f"✅ [AVAILABLE] {username} (only placeholder data)")
                        return
                    
                    # Unclear - needs manual check
                    self.update.emit(f"❓ [UNCLEAR] {username} ({signal_count} signals - manual check recommended)")
                    if self.debug:
                        self.update.emit(f"[DEBUG] URL for manual check: {url}")
                    
                    # Success - reset error counter
                    self.consecutive_errors = 0
                    break
                    
            except aiohttp.ClientConnectorError as e:
                self.consecutive_errors += 1
                if attempt < retries - 1:
                    if self.debug:
                        self.update.emit(f"[DEBUG] Connection failed, retrying {username}...")
                    await asyncio.sleep(3)
                    continue
                else:
                    self.update.emit(f"⚠️ [CONNECTION ERROR] {username}: Cannot reach TikTok")
                    await self.check_for_cooldown()
            except asyncio.TimeoutError:
                self.consecutive_errors += 1
                if attempt < retries - 1:
                    if self.debug:
                        self.update.emit(f"[DEBUG] Timeout, retrying {username}...")
                    await asyncio.sleep(2)
                    continue
                else:
                    self.update.emit(f"⏱️ [TIMEOUT] {username}")
                    await self.check_for_cooldown()
            except Exception as e:
                self.consecutive_errors += 1
                error_msg = str(e)[:80]
                # Check if it's a DNS/connection issue
                if 'nodename nor servname' in error_msg or 'ssl' in error_msg.lower() or 'connect' in error_msg.lower():
                    self.update.emit(f"⚠️ [CONNECTION ERROR] {username}: TikTok blocked or network issue")
                    await self.check_for_cooldown()
                else:
                    self.update.emit(f"⚠️ [ERROR] {username}: {error_msg}")
                break
            finally:
                async with lock:
                    self.count += 1
                self.pupdate.emit(self.count)

    async def check_for_cooldown(self):
        """Check if we need to pause due to consecutive errors"""
        if self.consecutive_errors >= self.max_errors_before_pause:
            cooldown_time = 15  # 15 seconds
            self.update.emit(f"\n🛑 COOLDOWN: {self.consecutive_errors} errors in a row detected!")
            self.update.emit(f"⏸️  Pausing for {cooldown_time} seconds to avoid being blocked...")
            
            for remaining in range(cooldown_time, 0, -1):
                if not self.running:  # Allow user to stop during cooldown
                    break
                self.update.emit(f"⏳ Resuming in {remaining} seconds...")
                await asyncio.sleep(1)
            
            self.update.emit(f"✅ Cooldown complete! Continuing...\n")
            self.consecutive_errors = 0  # Reset counter after cooldown

    async def main(self):
        sem = asyncio.Semaphore(2)  # Max 2 concurrent requests
        lock = asyncio.Lock()

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0"
        }

        # Use custom DNS resolver to avoid DNS issues
        connector = aiohttp.TCPConnector(
            limit=2, 
            ssl=False,  # Disable SSL verification if needed
            family=0,  # Allow both IPv4 and IPv6
            ttl_dns_cache=300
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=15)
        
        async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
            for i, username in enumerate(self.usernames):
                if not self.running:
                    break
                await self.check_user(username, sem, session, lock, i)
                await asyncio.sleep(2.5)  # Delay to avoid rate limits

# ------------------- GUI App ------------------- #
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TikTok Username Checker")
        self.setGeometry(150, 150, 1100, 800)
        self.thread = None
        self.initUI()

    def initUI(self):
        wid = QWidget(self)
        self.setCentralWidget(wid)
        main_layout = QVBoxLayout()
        wid.setLayout(main_layout)

        # Title
        title = QLabel("🎵 TikTok Username Checker")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("padding: 15px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00f2ea, stop:1 #ff0050); color: white; border-radius: 5px;")
        main_layout.addWidget(title)

        # Info Section
        info_group = QGroupBox("ℹ️ About TikTok Username Checker")
        info_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        info_layout = QVBoxLayout()
        
        instruction = QLabel("✨ No login required! This checker works without TikTok cookies.\n⚠️ Note: TikTok may rate limit or block requests. If you get connection errors:\n   • Try using a VPN\n   • Use mobile hotspot instead of WiFi\n   • Wait 5-10 minutes and try again")
        instruction.setWordWrap(True)
        instruction.setStyleSheet("background-color: #e7f3ff; padding: 10px; border-radius: 3px; color: #004085;")
        info_layout.addWidget(instruction)
        
        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)

        # Generator Section
        gen_group = QGroupBox("Step 1: Generate Random Usernames (Optional)")
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
        self.suffix_input.setPlaceholderText("e.g., .tt")
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
        self.gen_button.setStyleSheet("background-color: #00f2ea; color: black; padding: 8px; font-weight: bold;")
        row2.addWidget(self.gen_button)
        
        self.debug_checkbox = QCheckBox("🐛 Debug Mode (Detailed)")
        self.debug_checkbox.setToolTip("Show detailed analysis of each username")
        row2.addWidget(self.debug_checkbox)
        
        row2.addStretch()
        gen_layout.addLayout(row2)
        
        gen_group.setLayout(gen_layout)
        main_layout.addWidget(gen_group)

        # Input/Output Section
        io_group = QGroupBox("Step 2: Check Usernames")
        io_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        io_layout = QHBoxLayout()
        
        # Input side
        input_box = QVBoxLayout()
        input_label = QLabel("📝 Usernames to Check:")
        input_label.setStyleSheet("font-weight: bold;")
        input_box.addWidget(input_label)
        
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Enter usernames here\n(one per line)\n\nExample:\ncoolname\nawesomeuser\nviral123\n\n⚠️ No @ symbol needed")
        input_box.addWidget(self.input_text)
        
        # Output side
        output_box = QVBoxLayout()
        output_label = QLabel("📊 Results:")
        output_label.setStyleSheet("font-weight: bold;")
        output_box.addWidget(output_label)
        
        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setStyleSheet("background-color: #1a1a1a; color: #00f2ea; font-family: Consolas, Monaco, monospace; padding: 10px;")
        output_box.addWidget(self.output_text)
        
        io_layout.addLayout(input_box)
        io_layout.addLayout(output_box)
        io_group.setLayout(io_layout)
        main_layout.addWidget(io_group)

        # Control Buttons
        btn_layout = QHBoxLayout()
        
        self.start_button = QPushButton("▶️ START CHECKING")
        self.start_button.clicked.connect(self.start_clicked)
        self.start_button.setStyleSheet("background-color: #00f2ea; color: black; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_layout.addWidget(self.start_button)
        
        self.stop_button = QPushButton("⏹️ STOP")
        self.stop_button.clicked.connect(self.stop_clicked)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("background-color: #ff0050; color: white; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_layout.addWidget(self.stop_button)
        
        self.clear_button = QPushButton("🗑️ Clear Results")
        self.clear_button.clicked.connect(lambda: self.output_text.clear())
        self.clear_button.setStyleSheet("padding: 15px;")
        btn_layout.addWidget(self.clear_button)
        
        main_layout.addLayout(btn_layout)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("QProgressBar { text-align: center; height: 25px; } QProgressBar::chunk { background-color: #00f2ea; }")
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
            
            # Make sure it's valid (TikTok allows underscores and dots)
            if username and username.replace('_', '').replace('.', '').isalnum():
                generated.append(username)

        existing = self.input_text.toPlainText().strip()
        all_users = ("\n".join(generated) if not existing else existing + "\n" + "\n".join(generated))
        self.input_text.setText(all_users)
        
        self.status_label.setText(f"✅ Generated {len(generated)} usernames")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")

    def start_clicked(self):
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

        self.thread = Checker(usernames, ua, debug)
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
            # Remove @ if present
            if u.startswith('@'):
                u = u[1:]
            # TikTok allows letters, numbers, underscores, and dots
            if u and u.replace('_', '').replace('.', '').isalnum():
                usernames.append(u)
        return usernames

# ------------------- Run ------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())