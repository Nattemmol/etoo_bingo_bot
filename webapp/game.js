const LETTERS = ["B", "I", "N", "G", "O"];
const FREE_ROW = 2;
const FREE_COL = 2;
const FREE_INDEX = FREE_ROW * 5 + FREE_COL; // 12
const PM_MOD = 2147483647;
export const COLUMN_RANGES = {
  B: [1, 15],
  I: [16, 30],
  N: [31, 45],
  G: [46, 60],
  O: [61, 75],
};

export const RANGE_LABELS = {
  B: "1-15",
  I: "16-30",
  N: "31-45",
  G: "46-60",
  O: "61-75",
};

export function flatIndex(row, col) {
  return row * 5 + col;
}

export function isFreeCell(row, col) {
  return row === FREE_ROW && col === FREE_COL;
}

export function buildCalledBoard(container) {
  container.innerHTML = "";
  LETTERS.forEach((letter) => {
    const [min, max] = COLUMN_RANGES[letter];
    const rangeText = RANGE_LABELS[letter];

    const row = document.createElement("div");
    row.className = `called-board-row row-${letter.toLowerCase()}`;

    const label = document.createElement("div");
    label.className = "called-row-label";
    label.innerHTML = `<span class="c-letter">${letter}</span><span class="c-range">(${rangeText})</span>`;
    row.appendChild(label);

    const chipsWrap = document.createElement("div");
    chipsWrap.className = "called-row-chips";

    for (let n = min; n <= max; n++) {
      const chip = document.createElement("div");
      chip.className = "called-chip";
      chip.dataset.number = n;
      chip.textContent = n;
      chipsWrap.appendChild(chip);
    }
    row.appendChild(chipsWrap);
    container.appendChild(row);
  });
}

function pmNext(state) {
  const s = (16807 * state) % PM_MOD;
  return s || 1;
}

export function generateCardById(cardId) {
  let seed = ((cardId * 10007 + 7919) % PM_MOD) || 1;
  const card = [];
  for (let r = 0; r < 5; r++) {
    card.push(new Array(5).fill(null));
  }
  LETTERS.forEach((letter, col) => {
    const [min, max] = COLUMN_RANGES[letter];
    const pool = [];
    for (let i = min; i <= max; i++) pool.push(i);
    for (let i = pool.length - 1; i > 0; i--) {
      seed = pmNext(seed);
      const j = seed % (i + 1);
      const tmp = pool[i];
      pool[i] = pool[j];
      pool[j] = tmp;
    }
    const picks = pool.slice(0, 5);
    if (letter === "N") picks[2] = null;
    for (let r = 0; r < 5; r++) {
      card[r][col] = picks[r];
    }
  });
  return card;
}

export function updateCalledBoard(container, calledNumbers, latest = null) {
  const calledSet = new Set(calledNumbers);
  container.querySelectorAll(".called-chip").forEach((chip) => {
    const n = Number(chip.dataset.number);
    chip.classList.toggle("active", calledSet.has(n));
    chip.classList.toggle("latest", n === latest);
  });
}

export function generatePreviewCard() {
  const ranges = {
    B: [1, 15],
    I: [16, 30],
    N: [31, 45],
    G: [46, 60],
    O: [61, 75],
  };
  const picks = {};
  for (const l of LETTERS) {
    const [min, max] = ranges[l];
    const pool = [];
    for (let i = min; i <= max; i++) pool.push(i);
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    picks[l] = pool.slice(0, 5);
  }
  picks["N"][2] = null;

  const card = [];
  for (let r = 0; r < 5; r++) {
    const row = [];
    for (const l of LETTERS) {
      row.push(picks[l][r]);
    }
    card.push(row);
  }
  return card;
}

export function getWinningIndexes(card, marked, calledSet, rule = "line_corners") {
  const winIndexes = new Set();
  if (!card) return winIndexes;
  const flat = [].concat(...card);

  function isHit(idx) {
    if (idx === FREE_INDEX) return true;
    if (marked && marked.has(idx)) return true;
    const val = flat[idx];
    return val != null && calledSet && calledSet.has(val);
  }

  if (rule === "full") {
    let allHit = true;
    for (let i = 0; i < 25; i++) {
      if (!isHit(i)) {
        allHit = false;
        break;
      }
    }
    if (allHit) {
      for (let i = 0; i < 25; i++) winIndexes.add(i);
    }
    return winIndexes;
  }

  const lines = [
    [0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12, 13, 14], [15, 16, 17, 18, 19], [20, 21, 22, 23, 24],
    [0, 5, 10, 15, 20], [1, 6, 11, 16, 21], [2, 7, 12, 17, 22], [3, 8, 13, 18, 23], [4, 9, 14, 19, 24],
    [0, 6, 12, 18, 24], [4, 8, 12, 16, 20]
  ];

  if (rule === "line" || rule === "line_corners") {
    for (const line of lines) {
      if (line.every(isHit)) {
        line.forEach((idx) => winIndexes.add(idx));
      }
    }
  }

  if (rule === "corners" || rule === "line_corners") {
    const corners = [0, 4, 20, 24];
    if (corners.every(isHit)) {
      corners.forEach((idx) => winIndexes.add(idx));
    }
  }

  return winIndexes;
}

export function renderCard(container, card, marked, calledSet, onMark, interactive = true, rule = "line_corners") {
  container.innerHTML = "";
  const winningIndexes = getWinningIndexes(card, marked, calledSet, rule);

  LETTERS.forEach((letter) => {
    const head = document.createElement("div");
    head.className = `card-header-cell hdr-${letter.toLowerCase()}`;
    head.innerHTML = `<span class="hdr-letter">${letter}</span><span class="hdr-range">${RANGE_LABELS[letter]}</span>`;
    container.appendChild(head);
  });

  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 5; col++) {
      const cell = document.createElement("div");
      const value = card[row][col];
      const idx = flatIndex(row, col);
      const isFree = isFreeCell(row, col);

      cell.className = "card-cell";
      if (isFree) {
        cell.classList.add("free");
        cell.textContent = "FREE";
      } else {
        cell.textContent = value;
        if (calledSet && calledSet.has(value)) cell.classList.add("called");
        if (marked && marked.has(idx)) cell.classList.add("marked");

        if (interactive && onMark) {
          cell.addEventListener("click", () => {
            if (!calledSet || !calledSet.has(value)) {
              window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred("error");
              return;
            }
            onMark(row, col);
          });
        }
      }
      if (winningIndexes.has(idx)) {
        cell.classList.add("winning-cell");
      }
      container.appendChild(cell);
    }
  }
}

export function renderWinnerCard(container, card, calledSet, rule = "line_corners") {
  container.innerHTML = "";
  const winningIndexes = getWinningIndexes(card, null, calledSet, rule);
  winningIndexes.add(FREE_INDEX);

  LETTERS.forEach((letter) => {
    const head = document.createElement("div");
    head.className = `card-header-cell hdr-${letter.toLowerCase()}`;
    head.innerHTML = `<span class="hdr-letter">${letter}</span><span class="hdr-range">${RANGE_LABELS[letter]}</span>`;
    container.appendChild(head);
  });

  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 5; col++) {
      const cell = document.createElement("div");
      const value = card[row][col];
      const idx = flatIndex(row, col);
      const isFree = isFreeCell(row, col);

      cell.className = "card-cell winner-grid-cell";
      if (isFree) {
        cell.classList.add("free", "winning-cell");
        cell.textContent = "FREE";
      } else {
        cell.textContent = value;
        if (winningIndexes.has(idx)) {
          cell.classList.add("winning-cell", "marked");
        } else {
          cell.classList.add("dimmed-cell");
        }
      }
      container.appendChild(cell);
    }
  }
}

export function hasAnyWin(card, marked, calledSet, rule = "line_corners") {
  const winIndexes = getWinningIndexes(card, marked, calledSet, rule);
  return winIndexes.size > 0;
}

export function showScreen(id) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  document.getElementById(id)?.classList.add("active");
}

export function showError(message) {
  document.getElementById("error-message").textContent = message;
  showScreen("screen-error");
}
