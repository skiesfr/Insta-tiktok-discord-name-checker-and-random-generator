import sys, aiohttp, asyncio, random, string, re, json
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import QFont

# ------------------- Checker Thread ------------------- #
class Checker(QThread):
    update = pyqtSignal(str)
    pupdate = pyqtSignal(int)
    count = 0

    # Discord API endpoints
    POMELO_CHECK_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
    LEGACY_CHECK_URL = "https://discord.com/api/v9/users/@me"

    def __init__(self, usernames, token, user_agent, check_mode="pomelo", proxies=None, debug=False):
        super().__init__()
        self.usernames = usernames
        self.token = token
        self.user_agent = user_agent
        self.check_mode = check_mode  # "pomelo" or "legacy"
        self.proxies = proxies if proxies else []
        self.proxy_index = 0
        self.running = True
        self.debug = debug
        self.consecutive_errors = 0
        self.max_errors_before_pause = 3

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.main())
        finally:
            loop.close()

    def get_next_proxy(self):
        """Get the next proxy in rotation"""
        if not self.proxies:
            return None
        proxy = self.proxies[self.proxy_index]
        self.proxy_index = (self.proxy_index + 1) % len(self.proxies)
        return proxy

    def stop(self):
        self.running = False

    async def check_pomelo_username(self, username, session, proxy=None):
        """Check if a Pomelo (new) username is available"""
        try:
            payload = {"username": username}
            
            if self.debug and proxy:
                self.update.emit(f"[DEBUG] Using proxy: {proxy}")
            
            async with session.post(self.POMELO_CHECK_URL, json=payload, proxy=proxy, timeout=15) as resp:
                status = resp.status
                
                if self.debug:
                    self.update.emit(f"\n{'='*60}")
                    self.update.emit(f"[DEBUG] Checking: {username}")
                    self.update.emit(f"[DEBUG] Status Code: {status}")
                
                if status == 200:
                    data = await resp.json()
                    
                    if self.debug:
                        self.update.emit(f"[DEBUG] Response: {json.dumps(data, indent=2)}")
                    
                    taken = data.get("taken", True)
                    
                    if taken:
                        self.update.emit(f"❌ [TAKEN] {username}")
                    else:
                        self.update.emit(f"✅ [AVAILABLE] {username}")
                    
                    return not taken
                
                elif status == 429:
                    retry_after = resp.headers.get('Retry-After', '60')
                    try:
                        retry_seconds = int(float(retry_after))
                    except:
                        retry_seconds = 60
                    
                    self.update.emit(f"[RATE LIMIT] {username}: Waiting {retry_seconds}s...")
                    await self.cooldown(retry_seconds, "Rate limit hit")
                    return None
                
                elif status == 401:
                    self.update.emit(f"[AUTH ERROR] {username}: Invalid token")
                    return None
                
                else:
                    self.update.emit(f"[ERROR] {username}: Status {status}")
                    return None
                    
        except Exception as e:
            self.update.emit(f"[ERROR] {username}: {str(e)[:80]}")
            return None

    async def check_legacy_username(self, username, discriminator, session, proxy=None):
        """Check if a legacy username#discriminator is available"""
        try:
            # For legacy usernames, we need to try to change to it
            # This requires authentication
            payload = {
                "username": username,
                "discriminator": discriminator
            }
            
            if self.debug and proxy:
                self.update.emit(f"[DEBUG] Using proxy: {proxy}")
            
            async with session.patch(self.LEGACY_CHECK_URL, json=payload, proxy=proxy, timeout=15) as resp:
                status = resp.status
                
                if self.debug:
                    self.update.emit(f"\n{'='*60}")
                    self.update.emit(f"[DEBUG] Checking: {username}#{discriminator}")
                    self.update.emit(f"[DEBUG] Status Code: {status}")
                
                if status == 200:
                    self.update.emit(f"[AVAILABLE] {username}#{discriminator}")
                    return True
                
                elif status == 400:
                    data = await resp.json()
                    errors = data.get("errors", {})
                    
                    if "username" in errors:
                        self.update.emit(f"[TAKEN/INVALID] {username}#{discriminator}")
                    else:
                        self.update.emit(f"[ERROR] {username}#{discriminator}: {errors}")
                    return False
                
                elif status == 429:
                    self.update.emit(f"[RATE LIMIT] {username}#{discriminator}")
                    retry_after = int(resp.headers.get('Retry-After', 5))
                    await asyncio.sleep(retry_after)
                    return None
                
                else:
                    self.update.emit(f"[ERROR] {username}#{discriminator}: Status {status}")
                    return None
                    
        except Exception as e:
            self.update.emit(f"[ERROR] {username}: {str(e)[:80]}")
            return None

    async def check_user(self, username, sem, session, lock, idx):
        if not self.running:
            return

        async with sem:
            # Get next proxy from rotation
            proxy = self.get_next_proxy()
            
            try:
                if self.check_mode == "pomelo":
                    result = await self.check_pomelo_username(username, session, proxy)
                else:
                    # For legacy, split username#discriminator
                    if "#" in username:
                        uname, disc = username.split("#", 1)
                        result = await self.check_legacy_username(uname, disc, session, proxy)
                    else:
                        self.update.emit(f"[ERROR] {username}: Legacy mode requires format username#1234")
                        result = None
                
                if result is None:
                    self.consecutive_errors += 1
                    await self.check_for_cooldown()
                else:
                    self.consecutive_errors = 0
                    
            except aiohttp.ClientProxyConnectionError:
                self.update.emit(f"[PROXY ERROR] {username}: Could not connect via proxy")
                self.consecutive_errors += 1
            except asyncio.TimeoutError:
                self.consecutive_errors += 1
                self.update.emit(f"[TIMEOUT] {username}")
                await self.check_for_cooldown()
            except Exception as e:
                self.consecutive_errors += 1
                error_msg = str(e)[:80]
                self.update.emit(f"[ERROR] {username}: {error_msg}")
                await self.check_for_cooldown()
            finally:
                async with lock:
                    self.count += 1
                self.pupdate.emit(self.count)

    async def check_for_cooldown(self):
        """Check if we need to pause due to consecutive errors"""
        if self.consecutive_errors >= self.max_errors_before_pause:
            await self.cooldown(15, f"{self.consecutive_errors} errors in a row")
            self.consecutive_errors = 0

    async def cooldown(self, duration, reason):
        """Pause checking for a specified duration"""
        self.update.emit(f"\n COOLDOWN: {reason}!")
        self.update.emit(f"Pausing for {duration} seconds...")
        
        for remaining in range(duration, 0, -1):
            if not self.running:
                break
            self.update.emit(f"Resuming in {remaining} seconds...")
            await asyncio.sleep(1)
        
        self.update.emit(f"Cooldown complete! Continuing...\n")

    async def main(self):
        # Adjust concurrency based on proxy availability
        concurrent_limit = len(self.proxies) if self.proxies else 1
        concurrent_limit = min(concurrent_limit, 5)  # Cap at 5 concurrent
        
        sem = asyncio.Semaphore(concurrent_limit)
        lock = asyncio.Lock()
        
        if self.proxies:
            self.update.emit(f"Using {len(self.proxies)} proxies with {concurrent_limit} concurrent requests\n")
        else:
            self.update.emit(f"No proxies loaded - using direct connection (may hit rate limits)\n")

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
            "Authorization": self.token if self.check_mode == "legacy" else "",
            "X-Super-Properties": "eyJvcyI6IldpbmRvd3MiLCJicm93c2VyIjoiQ2hyb21lIiwiZGV2aWNlIjoiIiwic3lzdGVtX2xvY2FsZSI6ImVuLVVTIiwiYnJvd3Nlcl91c2VyX2FnZW50IjoiTW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTAuMDsgV2luNjQ7IHg2NCkgQXBwbGVXZWJLaXQvNTM3LjM2IChLSFRNTCwgbGlrZSBHZWNrbykgQ2hyb21lLzEyMC4wLjAuMCBTYWZhcmkvNTM3LjM2IiwiYnJvd3Nlcl92ZXJzaW9uIjoiMTIwLjAuMC4wIiwib3NfdmVyc2lvbiI6IjEwIiwicmVmZXJyZXIiOiIiLCJyZWZlcnJpbmdfZG9tYWluIjoiIiwicmVmZXJyZXJfY3VycmVudCI6IiIsInJlZmVycmluZ19kb21haW5fY3VycmVudCI6IiIsInJlbGVhc2VfY2hhbm5lbCI6InN0YWJsZSIsImNsaWVudF9idWlsZF9udW1iZXIiOjI1MDcxMCwiY2xpZW50X2V2ZW50X3NvdXJjZSI6bnVsbH0="
        }

        connector = aiohttp.TCPConnector(limit=concurrent_limit, ssl=True)
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
            for i, username in enumerate(self.usernames):
                if not self.running:
                    break
                await self.check_user(username, sem, session, lock, i)
                # Shorter delay if using proxies, longer if not
                delay = 1.0 if self.proxies else 3.0
                await asyncio.sleep(delay)

# ------------------- GUI App ------------------- #
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Discord Username Checker")
        self.setGeometry(150, 150, 1100, 800)
        self.thread = None
        self.initUI()

    def initUI(self):
        wid = QWidget(self)
        self.setCentralWidget(wid)
        main_layout = QVBoxLayout()
        wid.setLayout(main_layout)

        # Title
        title = QLabel("Discord Username Checker")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("padding: 15px; background-color: #5865F2; color: white; border-radius: 5px;")
        main_layout.addWidget(title)

        # Token Section
        token_group = QGroupBox("Step 1: Enter Your Discord Token (Optional for Pomelo)")
        token_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        token_layout = QVBoxLayout()
        
        instruction = QLabel("📌 Get token: Press F12 → Console → Type: (webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()")
        instruction.setWordWrap(True)
        instruction.setStyleSheet("background-color: #fff3cd; padding: 8px; border-radius: 3px; color: #856404;")
        token_layout.addWidget(instruction)
        
        token_input_layout = QHBoxLayout()
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Paste token here (required for legacy usernames)...")
        self.token_input.setEchoMode(QLineEdit.Password)
        token_input_layout.addWidget(self.token_input)
        
        show_btn = QPushButton("👁️")
        show_btn.setMaximumWidth(40)
        show_btn.clicked.connect(self.toggle_visibility)
        token_input_layout.addWidget(show_btn)
        token_layout.addLayout(token_input_layout)
        
        token_group.setLayout(token_layout)
        main_layout.addWidget(token_group)

        # Proxy Section
        proxy_group = QGroupBox("Step 2: Load Proxies (Optional - Helps Avoid Rate Limits)")
        proxy_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        proxy_layout = QVBoxLayout()
        
        proxy_info = QLabel("📡 Format: http://ip:port or http://user:pass@ip:port or socks5://ip:port (one per line)")
        proxy_info.setWordWrap(True)
        proxy_info.setStyleSheet("background-color: #d1ecf1; padding: 8px; border-radius: 3px; color: #0c5460;")
        proxy_layout.addWidget(proxy_info)
        
        proxy_input_layout = QHBoxLayout()
        self.proxy_input = QTextEdit()
        self.proxy_input.setPlaceholderText("http://proxy1.com:8080\nhttp://user:pass@proxy2.com:8080\nsocks5://proxy3.com:1080")
        self.proxy_input.setMaximumHeight(100)
        proxy_input_layout.addWidget(self.proxy_input)
        
        proxy_btn_layout = QVBoxLayout()
        self.load_proxy_btn = QPushButton("📂 Load from File")
        self.load_proxy_btn.clicked.connect(self.load_proxies_from_file)
        proxy_btn_layout.addWidget(self.load_proxy_btn)
        
        self.clear_proxy_btn = QPushButton("🗑️ Clear")
        self.clear_proxy_btn.clicked.connect(lambda: self.proxy_input.clear())
        proxy_btn_layout.addWidget(self.clear_proxy_btn)
        proxy_btn_layout.addStretch()
        
        proxy_input_layout.addLayout(proxy_btn_layout)
        proxy_layout.addLayout(proxy_input_layout)
        
        self.proxy_count_label = QLabel("📊 Proxies loaded: 0")
        self.proxy_count_label.setStyleSheet("font-style: italic; color: #666;")
        proxy_layout.addWidget(self.proxy_count_label)
        
        proxy_group.setLayout(proxy_layout)
        main_layout.addWidget(proxy_group)

        # Mode Selection
        mode_group = QGroupBox("Step 3: Select Check Mode")
        mode_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        mode_layout = QHBoxLayout()
        
        self.pomelo_radio = QRadioButton("Pomelo (New Usernames - No # needed)")
        self.pomelo_radio.setChecked(True)
        self.pomelo_radio.setToolTip("Check new Discord usernames without discriminators")
        mode_layout.addWidget(self.pomelo_radio)
        
        self.legacy_radio = QRadioButton("Legacy (Old format: username#1234)")
        self.legacy_radio.setToolTip("Check old Discord username#discriminator format (requires token)")
        mode_layout.addWidget(self.legacy_radio)
        
        mode_layout.addStretch()
        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)

        # Generator Section
        gen_group = QGroupBox("Step 4: Generate Random Usernames (Optional)")
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
        self.suffix_input.setPlaceholderText("e.g., _xd")
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
            "CamelCase (AbcDef)"
        ])
        self.pattern_combo.setMaximumWidth(200)
        row2.addWidget(self.pattern_combo)
        
        self.gen_button = QPushButton("🎲 Generate")
        self.gen_button.clicked.connect(self.generate_usernames)
        self.gen_button.setStyleSheet("background-color: #2196F3; color: white; padding: 8px; font-weight: bold;")
        row2.addWidget(self.gen_button)
        
        self.debug_checkbox = QCheckBox("🐛 Debug Mode")
        self.debug_checkbox.setToolTip("Show detailed API responses")
        row2.addWidget(self.debug_checkbox)
        
        row2.addStretch()
        gen_layout.addLayout(row2)
        
        gen_group.setLayout(gen_layout)
        main_layout.addWidget(gen_group)

        # Input/Output Section
        io_group = QGroupBox("Step 5: Check Usernames")
        io_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        io_layout = QHBoxLayout()
        
        # Input side
        input_box = QVBoxLayout()
        input_label = QLabel("📝 Usernames to Check:")
        input_label.setStyleSheet("font-weight: bold;")
        input_box.addWidget(input_label)
        
        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText("Enter usernames here\n(one per line)\n\nPomelo: username\nLegacy: username#1234")
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
        self.status_label = QLabel("✅ Ready to check Discord usernames")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #e0e0e0; border-radius: 3px;")
        main_layout.addWidget(self.status_label)

    def toggle_visibility(self):
        if self.token_input.echoMode() == QLineEdit.Password:
            self.token_input.setEchoMode(QLineEdit.Normal)
        else:
            self.token_input.setEchoMode(QLineEdit.Password)

    def load_proxies_from_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Load Proxy List", "", "Text Files (*.txt);;All Files (*)")
        if filename:
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    proxies = f.read()
                    self.proxy_input.setText(proxies)
                    proxy_count = len([p for p in proxies.strip().split('\n') if p.strip()])
                    self.proxy_count_label.setText(f"📊 Proxies loaded: {proxy_count}")
                    self.status_label.setText(f"✅ Loaded {proxy_count} proxies from file")
                    self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load proxies: {str(e)}")

    def get_proxies(self):
        """Parse and return list of proxies"""
        txt = self.proxy_input.toPlainText().strip()
        if not txt:
            return []
        
        proxies = []
        for line in txt.splitlines():
            proxy = line.strip()
            if proxy and (proxy.startswith('http://') or proxy.startswith('https://') or proxy.startswith('socks5://')):
                proxies.append(proxy)
        
        self.proxy_count_label.setText(f"📊 Proxies loaded: {len(proxies)}")
        return proxies

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
            
            # Add prefix and suffix
            username = prefix + username + suffix
            
            # Discord validation: 2-32 chars, alphanumeric + underscores, no consecutive periods/underscores
            username = re.sub(r'[^a-zA-Z0-9_.]', '', username)
            username = re.sub(r'\.\.+', '.', username)
            username = re.sub(r'__+', '_', username)
            username = username.strip('._')
            
            if 2 <= len(username) <= 32:
                generated.append(username)

        existing = self.input_text.toPlainText().strip()
        all_users = ("\n".join(generated) if not existing else existing + "\n" + "\n".join(generated))
        self.input_text.setText(all_users)
        
        self.status_label.setText(f"✅ Generated {len(generated)} usernames")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #c8e6c9; border-radius: 3px;")

    def start_clicked(self):
        check_mode = "pomelo" if self.pomelo_radio.isChecked() else "legacy"
        
        token = self.token_input.text().strip()
        if check_mode == "legacy" and not token:
            QMessageBox.warning(self, "Missing Token", "Legacy mode requires a Discord token!")
            return
        
        usernames = self.get_usernames()
        if not usernames:
            QMessageBox.warning(self, "No Usernames", "Please enter or generate usernames to check!")
            return
        
        # Get proxies
        proxies = self.get_proxies()
        
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        debug = self.debug_checkbox.isChecked()
        
        self.progress_bar.setMaximum(len(usernames))
        self.progress_bar.setValue(0)
        self.output_text.clear()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        if proxies:
            self.status_label.setText(f"🔄 Checking {len(usernames)} usernames with {len(proxies)} proxies...")
        else:
            self.status_label.setText(f"🔄 Checking {len(usernames)} usernames (no proxies - may be slower)...")
        self.status_label.setStyleSheet("padding: 8px; font-weight: bold; background-color: #fff9c4; border-radius: 3px;")

        self.thread = Checker(usernames, token, ua, check_mode, proxies, debug)
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
            u = line.strip()
            if u:
                usernames.append(u)
        return usernames

# ------------------- Run ------------------- #
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())
