import {
  buildCalledBoard,
  flatIndex,
  generateCardById,
  hasAnyWin,
  renderCard,
  renderWinnerCard,
  showError,
  showScreen,
  updateCalledBoard,
} from "./game.js";

const tg =
  window.Telegram?.WebApp || {
    ready() {},
    expand() {},
    close() {},
    initData: "",
    initDataUnsafe: {},
    HapticFeedback: undefined,
    platform: "web",
  };
tg.ready();
tg.expand();

const params = new URLSearchParams(window.location.search);
const roomId = params.get("room") || "room_play_10";

const MAX_CARDS = 2; // 1 user can have up to 2 cartela (cards) per round

const state = {
  room: null,
  phase: "connecting",
  isPlayer: false,
  balance: 0,
  myId: tg.initDataUnsafe?.user?.id,
  cardIds: [],
  cards: {}, // int cardId -> 5x5 grid
  marked: {}, // int cardId -> Set of tapped flat indexes (FREE center always included)
  takenCards: {}, // int cardId -> telegramId
  previewCardId: null,
  called: [],
  calledSet: new Set(),
  claimed: false, // the system auto-declared my BINGO (I am in the winner pool)
  lockedCards: new Set(), // card_ids locked due to false bingo calls
  windowUntil: null, // claim window deadline (ms)
  windowTimer: null,
  countdownInterval: null,
  lobbyCountdownDeadline: null,
  lobbyCountdownTimer: null,
};

const els = {
  userBalance: document.getElementById("user-balance"),
  rolePill: document.getElementById("role-pill"),
  roleText: document.getElementById("role-text"),
  scheduleBanner: document.getElementById("schedule-banner"),
  scheduleCountdown: document.getElementById("schedule-countdown"),
  lobbyPlayers: document.getElementById("lobby-players"),
  lobbyPot: document.getElementById("lobby-pot"),
  lobbyCountdown: document.getElementById("lobby-countdown"),
  lobbyRoomName: document.getElementById("lobby-room-name"),
  cardActionTitle: document.getElementById("card-action-title"),
  myCardsBadge: document.getElementById("my-cards-badge"),
  quickCardInput: document.getElementById("quick-card-input"),
  btnQuickPick: document.getElementById("btn-quick-pick"),
  btnRandomPick: document.getElementById("btn-random-pick"),
  cardPool: document.getElementById("card-pool"),
  cardErrorNotice: document.getElementById("card-error-notice"),
  gameRoomName: document.getElementById("game-room-name"),
  gamePot: document.getElementById("game-pot"),
  gamePlayers: document.getElementById("game-players"),
  gameBalls: document.getElementById("game-balls"),
  lastCallLetter: document.querySelector("#last-call .ball-letter"),
  lastCallNumber: document.querySelector("#last-call .ball-number"),
  calledBoard: document.getElementById("called-board"),
  gameBanner: document.getElementById("game-banner"),
  gameLayout: document.getElementById("game-layout"),
  cardsPanelTitle: document.getElementById("cards-panel-title"),
  cardList: document.getElementById("card-list"),
  gameSpectatorSection: document.getElementById("game-spectator-section"),
  btnBingo: document.getElementById("btn-bingo"),
  modalConfirm: document.getElementById("modal-confirm"),
  confirmTitle: document.getElementById("confirm-title"),
  confirmCardPreview: document.getElementById("confirm-card-preview"),
  confirmFee: document.getElementById("confirm-fee"),
  btnConfirmYes: document.getElementById("btn-confirm-yes"),
  btnConfirmNo: document.getElementById("btn-confirm-no"),
  modalUnselect: document.getElementById("modal-unselect"),
  unselectTitle: document.getElementById("unselect-title"),
  unselectCardPreview: document.getElementById("unselect-card-preview"),
  unselectFee: document.getElementById("unselect-fee"),
  btnUnselectYes: document.getElementById("btn-unselect-yes"),
  btnUnselectNo: document.getElementById("btn-unselect-no"),
  modalTaken: document.getElementById("modal-taken"),
  takenTitle: document.getElementById("taken-title"),
  btnTakenOk: document.getElementById("btn-taken-ok"),
  modalWinner: document.getElementById("modal-winner"),
  winnerTitle: document.getElementById("winner-title"),
  winnerMessage: document.getElementById("winner-message"),
  winnerCardsScroll: document.getElementById("winner-cards-scroll"),
  winnerCountdownText: document.getElementById("winner-countdown-text"),
  winnerProgressFill: document.getElementById("winner-progress-fill"),
  btnWinnerContinue: document.getElementById("btn-winner-continue"),
  themeToggleBtn: document.getElementById("theme-toggle-btn"),
};

// Theme Management (Defaults to Dark Mode)
function initTheme() {
  const saved = localStorage.getItem("goodbingo_theme") || "dark";
  setTheme(saved);
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("goodbingo_theme", theme);
  if (els.themeToggleBtn) {
    const icon = els.themeToggleBtn.querySelector(".theme-icon");
    if (icon) {
      icon.textContent = theme === "dark" ? "🌙" : "☀️";
    }
    els.themeToggleBtn.setAttribute(
      "title",
      theme === "dark" ? "Switch to Light Mode" : "Switch to Dark Mode"
    );
  }
}

if (els.themeToggleBtn) {
  els.themeToggleBtn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    setTheme(next);
    tg.HapticFeedback?.impactOccurred("light");
  });
}

initTheme();

buildCalledBoard(els.calledBoard);

function getBackendUrl() {
  const urlParam = params.get("api");
  if (urlParam) return urlParam.replace(/\/$/, "");
  if (window.BACKEND_URL) return window.BACKEND_URL.replace(/\/$/, "");
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    return "";
  }
  return "https://etoo-bingo-bot.onrender.com";
}

function apiUrl(path) {
  const base = getBackendUrl();
  return base ? `${base}${path}` : path;
}

function wsUrl() {
  const base = getBackendUrl();
  if (base) {
    const wsProto = base.startsWith("https:") ? "wss:" : "ws:";
    const host = base.replace(/^https?:\/\//, "");
    return `${wsProto}//${host}/ws/${roomId}`;
  }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/${roomId}`;
}

function updateBalanceDisplay(amount) {
  state.balance = amount;
  if (els.userBalance) {
    els.userBalance.textContent = Number(amount).toFixed(2);
  }
}

/**
 * Called when a deposit is credited and the balance goes up.
 * Shows a brief success toast and haptic feedback.
 */
function onDepositSuccess(creditedAmount, newBalance) {
  // Haptic feedback
  try { tg.HapticFeedback?.notificationOccurred("success"); } catch (_) {}

  // Show a brief toast notification
  let toast = document.getElementById("deposit-success-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "deposit-success-toast";
    toast.style.cssText =
      "position:fixed;top:20px;left:50%;transform:translateX(-50%);" +
      "background:#27ae60;color:#fff;padding:12px 24px;border-radius:12px;" +
      "font-weight:600;z-index:99999;box-shadow:0 4px 16px rgba(0,0,0,.3);" +
      "font-size:15px;transition:opacity 0.4s;pointer-events:none;";
    document.body.appendChild(toast);
  }
  toast.textContent = `✅ ${Number(creditedAmount).toFixed(2)} ETB ተጨምሯል! ቀሪ: ${Number(newBalance).toFixed(2)} ETB`;
  toast.style.opacity = "1";
  toast.style.display = "block";
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => { toast.style.display = "none"; }, 500);
  }, 3500);
}

function updateRoleDisplay(isPlayer) {
  state.isPlayer = isPlayer;
  if (!els.rolePill || !els.roleText) return;

  if (isPlayer) {
    els.rolePill.className = "role-pill role-player";
    els.roleText.textContent = "Player";
  } else {
    els.rolePill.className = "role-pill role-spectator";
    els.roleText.textContent = "Spectating";
  }
}

function updateGameStats(pot, players, ballsCount) {
  if (pot != null) {
    state.pot = Number(pot);
  }
  if (players != null) {
    state.playersCount = Number(players);
  }
  const potVal = state.pot != null ? state.pot : (state.room?.pot || 0);
  const playersVal = state.playersCount != null ? state.playersCount : (state.room?.players || Object.keys(state.takenCards || {}).length || 0);
  const count = ballsCount != null ? ballsCount : (state.called?.length || 0);

  if (els.gamePot) {
    els.gamePot.textContent = `${Number(potVal).toFixed(0)} ETB`;
  }
  if (els.lobbyPot) {
    els.lobbyPot.textContent = Number(potVal).toFixed(0);
  }
  if (els.gamePlayers) {
    els.gamePlayers.textContent = playersVal;
  }
  if (els.lobbyPlayers) {
    els.lobbyPlayers.textContent = playersVal;
  }
  if (els.gameBalls) {
    els.gameBalls.textContent = `${count}/75`;
  }
}

function showCardError(message) {
  if (!els.cardErrorNotice) return;
  els.cardErrorNotice.textContent = message;
  els.cardErrorNotice.classList.remove("hidden");
}

function hideCardError() {
  if (!els.cardErrorNotice) return;
  els.cardErrorNotice.textContent = "";
  els.cardErrorNotice.classList.add("hidden");
}

function setupScheduleTimer(seconds) {
  if (state.countdownInterval) {
    clearInterval(state.countdownInterval);
    state.countdownInterval = null;
  }

  if (!els.scheduleBanner || seconds <= 0) {
    els.scheduleBanner?.classList.add("hidden");
    return;
  }

  els.scheduleBanner.classList.remove("hidden");
  let remaining = seconds;

  function tick() {
    if (remaining <= 0) {
      clearInterval(state.countdownInterval);
      state.countdownInterval = null;
      els.scheduleBanner?.classList.add("hidden");
      return;
    }
    const str = formatCountdown(remaining);
    if (els.scheduleCountdown) {
      els.scheduleCountdown.textContent = `Starts in: ${str}`;
    }
    remaining--;
  }

  tick();
  state.countdownInterval = setInterval(tick, 1000);
}

// ---- Lobby "Starts in" countdown ----
function formatCountdown(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;

  const mmss = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  if (d > 0) {
    return `${String(d).padStart(2, "0")}d, ${String(h).padStart(2, "0")}:${mmss}`;
  }
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${mmss}`;
  }
  return mmss;
}

function syncLobbyCountdown(seconds) {
  if (seconds == null) return;
  state.lobbyCountdownDeadline = Date.now() + Math.max(0, seconds) * 1000;
  if (!state.lobbyCountdownTimer) {
    state.lobbyCountdownTimer = setInterval(renderLobbyCountdown, 250);
  }
  renderLobbyCountdown();
}

function renderLobbyCountdown() {
  if (!els.lobbyCountdown) return;
  const left = state.lobbyCountdownDeadline
    ? Math.max(0, Math.ceil((state.lobbyCountdownDeadline - Date.now()) / 1000))
    : 0;
  els.lobbyCountdown.textContent = formatCountdown(left);
  if (left <= 0) stopLobbyCountdown();
}

function stopLobbyCountdown() {
  if (state.lobbyCountdownTimer) {
    clearInterval(state.lobbyCountdownTimer);
    state.lobbyCountdownTimer = null;
  }
}

// ---- Card Pool ----
let poolChips = [];

function updatePoolChip(cardId) {
  const chip = poolChips[cardId - 1];
  if (!chip) return;
  const owner = state.takenCards[cardId];
  chip.classList.remove("available", "mine", "taken");
  if (owner == null) {
    chip.classList.add("available");
    chip.title = `መጫወቻ #${cardId} — available`;
  } else if (Number(owner) === Number(state.myId)) {
    chip.classList.add("mine");
    chip.title = `መጫወቻ #${cardId} — yours`;
  } else {
    chip.classList.add("taken");
    chip.title = `መጫወቻ #${cardId} — taken`;
  }
}

function buildPool() {
  const maxCards = state.room?.max_cards || (roomId === "room_super_50" ? 1500 : 450);
  if (els.cardActionTitle) els.cardActionTitle.textContent = `Card Pool (1-${maxCards})`;
  if (els.quickCardInput) {
    els.quickCardInput.max = maxCards;
    els.quickCardInput.placeholder = `መጫወቻ ቁጥር (1-${maxCards})`;
  }
  // Preserve the existing pool nodes across reconnects if size matches.
  if (poolChips.length === maxCards) {
    renderPool();
    return;
  }
  els.cardPool.innerHTML = "";
  poolChips = [];
  const frag = document.createDocumentFragment();
  for (let i = 1; i <= maxCards; i++) {
    const chip = document.createElement("div");
    chip.className = "pool-chip";
    chip.textContent = i;
    chip.dataset.cardId = String(i);
    frag.appendChild(chip);
    poolChips.push(chip);
  }
  els.cardPool.appendChild(frag);
  renderPool();
}

els.cardPool?.addEventListener("click", (event) => {
  const chip = event.target.closest(".pool-chip");
  if (chip) onPoolChipClick(Number(chip.dataset.cardId));
});
if (els.btnQuickPick) {
  els.btnQuickPick.addEventListener("click", () => {
    const val = parseInt(els.quickCardInput?.value, 10);
    const maxCards = state.room?.max_cards || (roomId === "room_super_50" ? 1500 : 450);
    if (!val || val < 1 || val > maxCards) {
      showCardError(`እባክዎ ከ 1 እስከ ${maxCards} ውስጥ ቁጥር ያስገቡ`);
      return;
    }
    onPoolChipClick(val);
  });
}

if (els.btnRandomPick) {
  els.btnRandomPick.addEventListener("click", () => {
    const maxCards = state.room?.max_cards || (roomId === "room_super_50" ? 1500 : 450);
    const available = [];
    for (let i = 1; i <= maxCards; i++) {
      if (state.takenCards[i] == null && !state.cardIds.includes(i)) {
        available.push(i);
      }
    }
    if (!available.length) {
      showCardError("ምንም ክፍት መጫወቻ አልተገኘም (No cards available)");
      return;
    }
    const picked = available[Math.floor(Math.random() * available.length)];
    onPoolChipClick(picked);
  });
}

function renderPool() {
  if (!poolChips.length) return;
  for (let id = 1; id <= poolChips.length; id++) updatePoolChip(id);
  if (els.myCardsBadge) els.myCardsBadge.textContent = `የተመረጡ መጫወቻዎች: ${state.cardIds.length}`;
}
function onPoolChipClick(cardId) {
  if (state.phase !== "lobby") {
    tg.HapticFeedback?.notificationOccurred("error");
    return;
  }
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showCardError("Connecting... please try again in a moment.");
    return;
  }
  hideCardError();

  // If already selected by me -> toggle / unselect immediately
  if (state.cardIds.includes(cardId)) {
    tg.HapticFeedback?.impactOccurred("medium");
    ws.send(JSON.stringify({ type: "unselect_card", card_id: cardId }));
    return;
  }

  // If taken by another person -> disabled
  if (state.takenCards[cardId] != null && state.takenCards[cardId] !== state.myId) {
    tg.HapticFeedback?.notificationOccurred("warning");
    showCardError(`መጫወቻ #${cardId} በሌላ ተጫዋች ተይዟል (Card #${cardId} is already taken)`);
    return;
  }

  // Check card limit (max 2)
  if (state.cardIds.length >= MAX_CARDS) {
    showCardError(`ከፍተኛ ${MAX_CARDS} መጫወቻዎች ብቻ በአንድ ዙር (Maximum ${MAX_CARDS} cards per round)`);
    tg.HapticFeedback?.notificationOccurred("error");
    return;
  }

  // Instant 1-tap selection!
  tg.HapticFeedback?.impactOccurred("medium");
  ws.send(JSON.stringify({ type: "select_card", card_id: cardId }));
}

if (els.btnUnselectYes) {
  els.btnUnselectYes.addEventListener("click", () => {
    const cardId = state.unselectCardId;
    closeModals();
    if (cardId == null) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      showCardError("Connecting... please try again in a moment.");
      return;
    }
    ws.send(JSON.stringify({ type: "unselect_card", card_id: cardId }));
  });
}

if (els.btnUnselectNo) {
  els.btnUnselectNo.addEventListener("click", closeModals);
}

els.btnConfirmNo.addEventListener("click", closeModals);
els.btnTakenOk.addEventListener("click", closeModals);

// ---- Game board + cards (side by side) ----
// The user marks called numbers on their cartela by tapping the cell.
// The FREE center is always marked.

let markSendTimeout;

function toggleMark(cardId, row, col) {
  if (state.phase !== "playing") return;
  const marks = state.marked[cardId] || new Set();
  const idx = flatIndex(row, col);
  let marked = true;
  if (marks.has(idx)) {
    marks.delete(idx);
    marked = false;
  } else {
    marks.add(idx);
  }
  state.marked[cardId] = marks;
  sendMark(cardId, row, col, marked);
  renderGameCards();
  tg.HapticFeedback?.selectionChanged();
}

function sendMark(cardId, row, col, marked) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  clearTimeout(markSendTimeout);
  markSendTimeout = setTimeout(() => {
    ws.send(JSON.stringify({ type: "mark", card_id: cardId, row, col, marked }));
  }, 50);
}

if (els.btnBingo) {
  els.btnBingo.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "bingo" }));
      tg.HapticFeedback?.notificationOccurred("success");
    }
  });
}

function renderGameCards() {
  if (!state.cardIds.length) return;
  els.cardList.className = state.cardIds.length === 2 ? "card-list two-cards" : "card-list";
  els.cardList.innerHTML = "";

  state.cardIds.forEach((id) => {
    const card = state.cards[id];
    if (!card) return;
    const isLocked = state.lockedCards && state.lockedCards.has(id);
    const block = document.createElement("div");
    block.className = `mini-card${isLocked ? " card-locked" : ""}`;

    const header = document.createElement("div");
    header.className = "mini-card-header";

    const title = document.createElement("div");
    title.className = "mini-card-title";
    title.textContent = `መጫወቻ #${id}`;
    header.appendChild(title);

    if (isLocked) {
      const badge = document.createElement("span");
      badge.className = "card-locked-badge";
      badge.textContent = "🔒 ተቆልፏል (Locked)";
      header.appendChild(badge);
    }
    block.appendChild(header);

    const grid = document.createElement("div");
    grid.className = "bingo-card mini-bingo-card";
    block.appendChild(grid);

    const marked = state.marked[id] || new Set();
    const rule = state.room?.bingo_rule || "line_corners";
    renderCard(
      grid,
      card,
      marked,
      state.calledSet,
      (row, col) => {
        if (isLocked) return;
        toggleMark(id, row, col);
      },
      !isLocked,
      rule
    );

    if (!isLocked && hasAnyWin(card, marked, state.calledSet, rule)) {
      block.classList.add("has-bingo");
    }

    // BINGO Button per card — ALWAYS OPEN & ACTIVE from the start of the game
    const bingoBtn = document.createElement("button");
    bingoBtn.className = "btn btn-card-bingo";
    if (isLocked) {
      bingoBtn.disabled = true;
      bingoBtn.textContent = "🔒 መጫወቻው ተቆልፏል (Locked)";
    } else {
      bingoBtn.textContent = "🎉 BINGO!";
      bingoBtn.addEventListener("click", () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "bingo", card_id: id }));
          tg.HapticFeedback?.impactOccurred("medium");
        }
      });
    }
    block.appendChild(bingoBtn);

    els.cardList.appendChild(block);
  });
}

function showBanner(text, error = false) {
  els.gameBanner.textContent = text;
  els.gameBanner.classList.remove("hidden");
  els.gameBanner.classList.toggle("banner-error", error);
}

function hideBanner() {
  els.gameBanner?.classList.add("hidden");
}

function refreshClaimBanner() {
  const left = state.windowUntil ? Math.max(0, Math.ceil((state.windowUntil - Date.now()) / 1000)) : 0;
  if (left > 0) {
    showBanner(`⏳ BINGO claimed — ${left}s left for other winners to claim too!`);
  } else {
    showBanner("⏳ Waiting for results…");
  }
}

function startWindowTimer() {
  if (state.windowTimer) return;
  refreshClaimBanner();
  state.windowTimer = setInterval(() => {
    if (state.windowUntil && Date.now() >= state.windowUntil) {
      clearInterval(state.windowTimer);
      state.windowTimer = null;
      refreshClaimBanner();
    } else {
      refreshClaimBanner();
    }
  }, 1000);
}

function stopWindowTimer() {
  if (state.windowTimer) {
    clearInterval(state.windowTimer);
    state.windowTimer = null;
  }
  state.windowUntil = null;
}

function setupGameScreen() {
  if (state.cardIds.length) {
    els.gameLayout.classList.remove("spectator");
    els.gameSpectatorSection.classList.add("hidden");
    els.cardsPanelTitle.textContent = "Your Cards";
    renderGameCards();
  } else {
    els.gameLayout.classList.add("spectator");
    els.gameSpectatorSection.classList.remove("hidden");
    els.cardsPanelTitle.textContent = "Spectator";
    els.cardList.innerHTML = "";
  }
}

function connect() {
  const url = wsUrl();
  console.log("Connecting to WebSocket:", url);
  const socket = new WebSocket(url);

  socket.onopen = () => {
    console.log("WebSocket connected!");
    reconnectDelay = 1000;
    socket.send(JSON.stringify({ type: "join", initData: tg.initData || "" }));
  };

  socket.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleMessage(socket, msg);
    } catch (err) {
      console.error("Error parsing message:", err);
    }
  };

  socket.onerror = (err) => {
    console.warn("WebSocket error:", err);
  };

  socket.onclose = () => {
    console.log("WebSocket closed, attempting reconnect...");
    reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
    setTimeout(() => {
      if (state.phase === "connecting" || !ws || ws.readyState === WebSocket.CLOSED) {
        ws = connect();
      }
    }, reconnectDelay + Math.random() * 500);
  };

  return socket;
}

let reconnectDelay = 1000;
let ws;

function updateCalledCell(number) {
  document.querySelectorAll(`.card-cell[data-value="${number}"]`).forEach((cell) => {
    cell.classList.add("called");
  });
}

function handleMessage(socket, msg) {
  switch (msg.type) {
    case "balance": {
      if (msg.balance != null) {
        const oldBal = state.balance;
        const newBal = Number(msg.balance);
        updateBalanceDisplay(newBal);
        // If balance went up, show success feedback (deposit credited)
        if (newBal > oldBal) {
          onDepositSuccess(newBal - oldBal, newBal);
        }
      }
      break;
    }

    case "init": {
      state.room = msg.room;
      state.phase = msg.room.phase === "playing" ? "playing" : "lobby";
      state.takenCards = msg.room.taken_cards || {};
      state.called = msg.room.called || [];
      state.calledSet = new Set(state.called);
      if (msg.user) {
        state.myId = msg.user.id || state.myId;
        updateBalanceDisplay(msg.user.balance);
      }

      state.cardIds = Array.isArray(msg.card_ids) ? msg.card_ids.map(Number) : [];
      state.cards = {};
      if (msg.cards) {
        Object.entries(msg.cards).forEach(([id, card]) => {
          state.cards[Number(id)] = card;
        });
      }
      if (msg.marks) {
          for (const [cardId, indices] of Object.entries(msg.marks)) {
              state.marked[cardId] = new Set(indices);
          }
      }
      state.isPlayer = state.cardIds.length > 0;
      updateRoleDisplay(state.isPlayer);

      if (els.lobbyRoomName) els.lobbyRoomName.textContent = `${msg.room.name} — ${msg.room.entry_fee} ETB`;
      if (els.gameRoomName) els.gameRoomName.textContent = msg.room.name;
      if (els.lobbyPlayers) els.lobbyPlayers.textContent = msg.room.players;
      if (els.lobbyPot) els.lobbyPot.textContent = Number(msg.room.pot).toFixed(0);
      updateGameStats(msg.room.pot, msg.room.players, (msg.room.called || []).length);
      if (msg.room.phase === "lobby") {
        syncLobbyCountdown(msg.room.countdown);
      }

      if (!msg.room.is_open && msg.room.seconds_until_open > 0) {
        setupScheduleTimer(msg.room.seconds_until_open);
      } else {
        els.scheduleBanner?.classList.add("hidden");
      }

      buildPool();
      renderPool();

      if (msg.room.phase === "playing") {
        if (msg.room.latest_call) {
          if (els.lastCallLetter) els.lastCallLetter.textContent = msg.room.latest_call.letter;
          if (els.lastCallNumber) els.lastCallNumber.textContent = msg.room.latest_call.number;
          updateCalledBoard(els.calledBoard, state.called, msg.room.latest_call.number);
        } else {
          updateCalledBoard(els.calledBoard, state.called);
        }
        setupGameScreen();
        showScreen("screen-game");
      } else {
        showScreen("screen-lobby");
      }
      break;
    }

    case "lobby":
    case "room_stats": {
      if (msg.players != null) els.lobbyPlayers.textContent = msg.players;
      if (msg.pot != null) {
        els.lobbyPot.textContent = Number(msg.pot).toFixed(0);
      }
      updateGameStats(msg.pot, msg.players, state.called.length);
      if (msg.deadline != null) {
        let leftSecs = 0;
        if (msg.deadline > 1e11) {
          // Absolute timestamp in milliseconds
          leftSecs = Math.max(0, (msg.deadline - Date.now()) / 1000);
        } else if (msg.deadline > 1e8) {
          // Absolute timestamp in seconds
          leftSecs = Math.max(0, msg.deadline - Date.now() / 1000);
        } else {
          // Relative seconds remaining
          leftSecs = Math.max(0, msg.deadline);
        }
        syncLobbyCountdown(leftSecs);
      } else if (msg.countdown != null) {
        syncLobbyCountdown(msg.countdown);
      }
      if (msg.taken_cards) {
        state.takenCards = msg.taken_cards;
        renderPool();
      }
      break;
    }

    case "card_confirmed": {
      const cid = msg.card_id;
      state.cards[cid] = msg.card;
      state.cardIds = Array.isArray(msg.card_ids) ? msg.card_ids.map(Number) : [...state.cardIds, cid];
      state.takenCards[cid] = state.myId;
      state.isPlayer = true;
      updateRoleDisplay(true);
      updateBalanceDisplay(msg.balance);
      renderPool();
      if (msg.pot != null) {
        els.lobbyPot.textContent = Number(msg.pot).toFixed(0);
      }
      updateGameStats(msg.pot, msg.players, state.called.length);
      tg.HapticFeedback?.notificationOccurred("success");
      break;
    }

    case "card_unselected": {
      const cid = msg.card_id;
      delete state.cards[cid];
      state.cardIds = Array.isArray(msg.card_ids)
        ? msg.card_ids.map(Number)
        : state.cardIds.filter((id) => id !== cid);
      delete state.takenCards[cid];
      updatePoolChip(cid);
      state.isPlayer = Boolean(msg.is_player);
      updateRoleDisplay(state.isPlayer);
      updateBalanceDisplay(msg.balance);
      renderPool();
      if (msg.pot != null) {
        els.lobbyPot.textContent = Number(msg.pot).toFixed(0);
      }
      if (msg.players != null) {
        els.lobbyPlayers.textContent = msg.players;
      }
      updateGameStats(msg.pot, msg.players, state.called.length);
      showBanner(`✅ መጫወቻ #${cid} ተሰርዟል። ${msg.refund_amount || 10} ETB ተመላሽ ተደርጓል።`);
      setTimeout(hideBanner, 3000);
      tg.HapticFeedback?.notificationOccurred("success");
      break;
    }

    case "card_released": {
      if (msg.card_id != null) {
        delete state.takenCards[msg.card_id];
        updatePoolChip(msg.card_id);
      }
      if (msg.players != null) els.lobbyPlayers.textContent = msg.players;
      if (msg.pot != null) {
        els.lobbyPot.textContent = Number(msg.pot).toFixed(0);
      }
      updateGameStats(msg.pot, msg.players, state.called.length);
      break;
    }

    case "card_taken": {
      if (msg.card_id != null && msg.telegram_id != null && msg.telegram_id !== state.myId) {
        state.takenCards[msg.card_id] = msg.telegram_id;
        updatePoolChip(msg.card_id);
      }
      if (msg.players != null) els.lobbyPlayers.textContent = msg.players;
      if (msg.pot != null) {
        els.lobbyPot.textContent = Number(msg.pot).toFixed(0);
      }
      updateGameStats(msg.pot, msg.players, state.called.length);
      break;
    }

    case "card_taken_error":
      openTakenModal(msg.card_id);
      break;

    case "card_error":
      showCardError(msg.message);
      if (msg.balance != null) updateBalanceDisplay(msg.balance);
      tg.HapticFeedback?.notificationOccurred("error");
      break;

    case "start":
      if (state.countdownInterval) {
        clearInterval(state.countdownInterval);
        state.countdownInterval = null;
      }
      state.phase = "playing";
      state.lockedCards = new Set();
      stopLobbyCountdown();
      els.gameRoomName.textContent = state.room?.name || "GoodBingo";
      updateGameStats(msg.pot, msg.players, 0);
      setupGameScreen();
      showScreen("screen-game");
      tg.HapticFeedback?.impactOccurred("medium");
      break;

    case "call":
      state.called = msg.called;
      state.calledSet = new Set(msg.called);
      els.lastCallLetter.textContent = msg.letter;
      els.lastCallNumber.textContent = msg.number;
      updateGameStats(null, null, msg.called.length);
      updateCalledBoard(els.calledBoard, msg.called, msg.number);
      if (state.isPlayer) {
        updateCalledCell(msg.number);
      }
      tg.HapticFeedback?.impactOccurred("light");
      break;

    case "bingo_result":
      // Legacy from manual BINGO presses; auto-detection is authoritative now.
      if (msg.valid && msg.status === "accepted") {
        if (state.windowUntil == null) {
          state.windowUntil = Date.now() + (msg.window_seconds || 5) * 1000;
        }
        startWindowTimer();
        tg.HapticFeedback?.notificationOccurred("success");
      }
      break;

    case "card_locked": {
      if (msg.card_id != null) {
        state.lockedCards.add(msg.card_id);
      }
      (msg.locked_cards || []).forEach((cid) => state.lockedCards.add(cid));
      showBanner(msg.message || "❌ False Bingo! Card locked.", true);
      tg.HapticFeedback?.notificationOccurred("error");
      renderGameCards();
      break;
    }

    case "player_card_locked": {
      if (msg.name) {
        showBanner(`⚠️ ${msg.name} መጫወቻ #${msg.card_id} ላይ የተሳሳተ BINGO ብለው መጫወቻቸው ተቆልፏል!`, true);
      }
      break;
    }

    case "bingo_claim": {
      if (state.windowUntil == null) {
        state.windowUntil = Date.now() + (msg.window_seconds || 5) * 1000;
        startWindowTimer();
      }
      if (msg.claimant_id === state.myId) {
        state.claimed = true;
        showBanner("🎉 Bingo! You are in the winner pool — መልካም እድል!");
        tg.HapticFeedback?.notificationOccurred("success");
      } else {
        showBanner(`🎉 ${msg.claimant_name} has Bingo! ${msg.window_seconds || 5}s remain to claim too.`);
        tg.HapticFeedback?.impactOccurred("medium");
      }
      break;
    }

    case "winner":
      state.phase = "done";
      stopWindowTimer();
      stopLobbyCountdown();
      openWinnerModal(msg);
      break;

    case "game_over":
      showError("All numbers were called. No winner this round.");
      state.phase = "done";
      stopWindowTimer();
      stopLobbyCountdown();
      break;

    case "round_reset": {
      state.cardIds = [];
      state.cards = {};
      state.marked = {};
      state.lockedCards = new Set();
      state.isPlayer = false;
      state.called = [];
      state.calledSet = new Set();
      state.phase = "lobby";
      state.claimed = false;

      if (state.winnerTimer) {
        clearInterval(state.winnerTimer);
        state.winnerTimer = null;
      }

      updateRoleDisplay(false);
      hideCardError();
      hideBanner();
      stopWindowTimer();
      stopLobbyCountdown();
      buildCalledBoard(els.calledBoard);
      els.lastCallLetter.textContent = "-";
      els.lastCallNumber.textContent = "-";

      els.modalWinner?.classList.add("hidden");

      if (msg.room) {
        state.room = msg.room;
        state.takenCards = msg.room.taken_cards || {};
        els.lobbyPlayers.textContent = msg.room.players;
        els.lobbyPot.textContent = Number(msg.room.pot).toFixed(0);
        updateGameStats(msg.room.pot, msg.room.players, 0);
        if (msg.room.phase === "lobby") {
          syncLobbyCountdown(msg.room.countdown);
        }
        if (!msg.room.is_open && msg.room.seconds_until_open > 0) {
          setupScheduleTimer(msg.room.seconds_until_open);
        } else {
          els.scheduleBanner?.classList.add("hidden");
        }
      } else {
        state.takenCards = {};
      }

      buildPool();
      renderPool();
      showScreen("screen-lobby");
      break;
    }

    case "error":
      showError(msg.message);
      state.phase = "done";
      break;

    default:
      break;
  }
}

function openWinnerModal(msg) {
  if (!els.modalWinner) return;
  const winners = Array.isArray(msg.winners) ? msg.winners : [];
  const pot = Number(msg.pot || 0);
  const calledSet = new Set(msg.called || state.called || []);
  const mine = winners.find((w) => w.telegram_id === state.myId);
  const badgeIcon = els.modalWinner.querySelector(".winner-trophy-badge");

  if (msg.all_locked) {
    if (badgeIcon) badgeIcon.textContent = "🔒";
    if (els.winnerTitle) els.winnerTitle.textContent = "ሁሉም መጫወቻዎች ተቆልፈዋል!";
    if (els.winnerMessage) {
      if (msg.refund_amount && Number(msg.refund_amount) > 0) {
        els.winnerMessage.textContent = `ለብቻዎ ስለነበሩ የተከፈለው ${Number(msg.refund_amount).toFixed(0)} ETB ሙሉ በሙሉ ተመልሷል።`;
      } else {
        els.winnerMessage.textContent = msg.message || "ሁሉም መጫወቻዎች ተቆልፈዋል — ቀጣዩን ዙር ይቀላቀሉ";
      }
    }
    if (els.winnerCardsScroll) els.winnerCardsScroll.innerHTML = "";
    if (msg.balance != null) {
      state.balance = Number(msg.balance);
      if (els.userBalance) els.userBalance.textContent = state.balance.toFixed(2);
    }
  } else if (mine && mine.prize != null) {
    if (badgeIcon) badgeIcon.textContent = "🏆";
    if (els.winnerTitle) els.winnerTitle.textContent = "🏆 እንኳን ደስ አሎት!";
    if (els.winnerMessage) {
      els.winnerMessage.textContent = `በ Card #${mine.card_id} (${mine.pattern}) ${Number(mine.prize).toFixed(2)} ETB አሸንፈዋል!`;
    }
  } else if (winners.length > 0) {
    if (badgeIcon) badgeIcon.textContent = "🏆";
    if (els.winnerTitle) els.winnerTitle.textContent = "🎉 ዙሩ ተጠናቋል!";
    if (els.winnerMessage) {
      const wCount = winners.length;
      els.winnerMessage.textContent = `${wCount > 1 ? `${wCount} አሸናፊዎች` : "አሸናፊ"} ${pot.toFixed(2)} ETB ተሸልመዋል`;
    }
  } else {
    if (badgeIcon) badgeIcon.textContent = "🏁";
    if (els.winnerTitle) els.winnerTitle.textContent = "ዙሩ ተጠናቋል";
    if (els.winnerMessage) els.winnerMessage.textContent = "ምንም አሸናፊ አልተገኘም";
  }

  // Populate scrollable winner cards
  if (els.winnerCardsScroll && !msg.all_locked) {
    els.winnerCardsScroll.innerHTML = "";
    winners.forEach((w) => {
      const cardGrid = w.card || generateCardById(w.card_id);
      const isMe = w.telegram_id === state.myId;

      const cardItem = document.createElement("div");
      cardItem.className = "winner-card-item";

      const patternAmharic = {
        row: "መስመር (Row)",
        column: "አምድ (Column)",
        diagonal: "ሰያፍ (Diagonal)",
        corners: "4 መአዘን (Corners)",
        line_corners: "መስመር / መአዘን",
        line: "መስመር (Line)",
        full: "ሙሉ ካርድ (Full)",
      }[w.pattern] || w.pattern || "BINGO";

      cardItem.innerHTML = `
        <div class="winner-card-header">
          <div class="winner-name-badge">
            <span style="font-size:1.1rem;">${isMe ? "👑" : "👤"}</span>
            <div>
              <div class="winner-name-text">${w.name || `ተጫዋች #${w.telegram_id}`} ${isMe ? "(እርስዎ)" : ""}</div>
              <div class="winner-card-id-text">መጫወቻ #${w.card_id}</div>
            </div>
          </div>
          <div class="winner-tags">
            <span class="winner-prize-tag">${Number(w.prize || pot).toFixed(0)} ETB</span>
            <span class="winner-pattern-tag">${patternAmharic}</span>
          </div>
        </div>
        <div class="winner-mini-grid"></div>
      `;

      const gridEl = cardItem.querySelector(".winner-mini-grid");
      const rule = state.room?.bingo_rule || "line_corners";
      renderWinnerCard(gridEl, cardGrid, calledSet, rule);
      els.winnerCardsScroll.appendChild(cardItem);
    });
  }

  // 10-second countdown with progress bar
  const duration = msg.wait_seconds || 10;
  const startTime = Date.now();
  const endTime = startTime + duration * 1000;

  if (state.winnerTimer) {
    clearInterval(state.winnerTimer);
    state.winnerTimer = null;
  }

  function updateWinnerTimer() {
    const now = Date.now();
    const remainingMs = Math.max(0, endTime - now);
    const remainingSec = Math.ceil(remainingMs / 1000);
    const progress = Math.max(0, Math.min(100, (remainingMs / (duration * 1000)) * 100));

    if (els.winnerCountdownText) {
      els.winnerCountdownText.textContent = `ቀጣዩ ዙር በ ${remainingSec} ሰከንድ ይጀምራል...`;
    }
    if (els.winnerProgressFill) {
      els.winnerProgressFill.style.width = `${progress}%`;
    }

    if (remainingMs <= 0) {
      if (state.winnerTimer) {
        clearInterval(state.winnerTimer);
        state.winnerTimer = null;
      }
      els.modalWinner?.classList.add("hidden");
      showScreen("screen-lobby");
    }
  }

  updateWinnerTimer();
  state.winnerTimer = setInterval(updateWinnerTimer, 100);

  els.modalWinner.classList.remove("hidden");
  tg.HapticFeedback?.notificationOccurred("success");
}

els.btnWinnerContinue.addEventListener("click", () => {
  if (state.winnerTimer) {
    clearInterval(state.winnerTimer);
    state.winnerTimer = null;
  }
  els.modalWinner?.classList.add("hidden");
  showScreen("screen-lobby");
});

buildPool();

ws = connect();

// Auto-sync user balance with server
async function syncUserBalance() {
  if (!tg.initData) return;
  try {
    const resp = await fetch(apiUrl("/api/user/me"), {
      headers: { "X-Telegram-Init-Data": tg.initData },
    });
    const data = await resp.json();
    if (data.ok && data.balance != null) {
      const oldBal = state.balance;
      const newBal = Number(data.balance);
      updateBalanceDisplay(newBal);
      if (oldBal > 0 && newBal > oldBal) {
        onDepositSuccess(newBal - oldBal, newBal);
      }
    }
  } catch (err) {
    console.warn("Error syncing user balance:", err);
  }
}

// Sync balance on return or tab focus with rapid polling
const isReturn = window.location.pathname.includes("/return") || params.get("action") === "deposit" || params.get("action") === "withdraw";
if (isReturn) {
  setTimeout(syncUserBalance, 200);
  setTimeout(syncUserBalance, 1000);
  setTimeout(syncUserBalance, 2500);
  setTimeout(syncUserBalance, 5000);
}

// Listen for tab focus or returning from browser/Telegram
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    syncUserBalance();
  }
});

window.addEventListener("focus", () => {
  syncUserBalance();
});

// Initial balance sync on startup
syncUserBalance();

// Keep-alive ping every 30s
setInterval(() => {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "ping" }));
  }
}, 30000);