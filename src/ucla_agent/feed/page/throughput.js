"use strict";

// The #throughput band: how much of the pool of 20 orders a turn the agent uses, as figures and as a live chart.
(() => {
  const band = document.getElementById("throughput");
  const { element, clock, number } = Feed;
  const CHART_SECONDS = 120;
  const POOL = 20;
  const PAD = { left: 34, right: 8, top: 8, bottom: 20 };
  const COLUMN_GAP = 2;
  const MAX_COLUMN = 24;
  const DATA_END_RADIUS = 4;
  const COLORS = { orders: "#2b5ba8", hover: "#5a82c4", capacity: "#1d2630", grid: "#d5d9cf", text: "#5b6670" };
  const SPANS = [["last_10s", "Last 10 s"], ["match", "Match"]];
  const COLUMNS = [
    ["Orders/s", (span) => number(span.orders_per_s, 1)],
    ["Batches/s", (span) => number(span.batches_per_s, 1)],
    ["Commands/s", (span) => number(span.commands_per_s, 1)],
    ["Orders per batch", (span) => `${number(span.mean_orders_per_batch, 1)} / ${span.pool_orders}`],
    ["Batches at 20", (span) => (typeof span.at_limit_percent === "number" ? `${number(span.at_limit_percent, 1)}%` : "–")],
    ["Commands per batch", (span) => `${number(span.mean_commands_per_batch, 1)} / ${span.command_limit}`],
    ["Rejected/s", (span) => number(span.rejected_per_s, 2)],
  ];

  const figuresBody = element("tbody");
  const figuresTable = element(
    "table",
    { class: "rates" },
    element("thead", {}, element("tr", {}, element("th", { scope: "col" }, "Span"), ...COLUMNS.map(([name]) => element("th", { scope: "col" }, name)))),
    figuresBody,
  );
  const canvas = element("canvas", { id: "throughput-chart", tabindex: "0", role: "img" });
  const tip = element("div", { class: "chart-tip", hidden: true });
  const secondsBody = element("tbody");
  const legend = element(
    "div",
    { class: "legend" },
    element("span", { class: "key" }, element("span", { class: "swatch" }), "orders sent in the second"),
    element("span", { class: "key" }, element("span", { class: "line-key" }), "pool capacity: 20 orders for each batch of the second"),
  );
  band.append(
    element("h2", {}, "Orders against the pool of 20 a batch"),
    element(
      "div",
      { class: "throughput-body" },
      figuresTable,
      element(
        "figure",
        { class: "chart" },
        legend,
        element("div", { class: "plot" }, canvas, tip),
        element(
          "details",
          {},
          element("summary", {}, "The last 120 s as a table"),
          element("table", { class: "seconds" }, element("thead", {}, element("tr", {}, ...["Game time", "Orders", "Batches", "Pool"].map((name) => element("th", { scope: "col" }, name)))), secondsBody),
        ),
      ),
    ),
  );
  band.hidden = true;

  let series = [];
  let hovered = null;

  function counted(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
  }

  function renderFigures(figures) {
    figuresBody.replaceChildren(
      ...SPANS.map(([key, label]) =>
        element("tr", {}, element("th", { scope: "row" }, label), ...COLUMNS.map(([, value]) => element("td", {}, value(figures[key])))),
      ),
    );
  }

  function renderSecondsTable() {
    secondsBody.replaceChildren(
      ...series
        .slice()
        .reverse()
        .map((second) => element("tr", {}, element("td", {}, clock(second.second)), element("td", {}, second.orders), element("td", {}, second.batches), element("td", {}, POOL * second.batches))),
    );
  }

  // The geometry of the plot: one slot per second of the window, newest at the right.
  function layout() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const plotWidth = width - PAD.left - PAD.right;
    const plotHeight = height - PAD.top - PAD.bottom;
    const slot = plotWidth / CHART_SECONDS;
    const peak = Math.max(POOL, ...series.map((second) => Math.max(second.orders, POOL * second.batches)));
    const top = Math.ceil(peak / POOL) * POOL;
    return {
      width,
      height,
      slot,
      column: Math.max(1, Math.min(MAX_COLUMN, slot - COLUMN_GAP)),
      top,
      x: (index) => PAD.left + (CHART_SECONDS - series.length + index) * slot,
      y: (value) => PAD.top + plotHeight * (1 - value / top),
    };
  }

  function columnPath(context, left, width, top, bottom) {
    const radius = Math.min(DATA_END_RADIUS, width / 2, bottom - top);
    context.beginPath();
    context.moveTo(left, bottom);
    context.lineTo(left, top + radius);
    context.arcTo(left, top, left + radius, top, radius);
    context.lineTo(left + width - radius, top);
    context.arcTo(left + width, top, left + width, top + radius, radius);
    context.lineTo(left + width, bottom);
    context.closePath();
  }

  function draw() {
    const ratio = window.devicePixelRatio || 1;
    const geometry = layout();
    canvas.width = Math.round(geometry.width * ratio);
    canvas.height = Math.round(geometry.height * ratio);
    const context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, geometry.width, geometry.height);
    context.font = "11px 'Segoe UI', system-ui, sans-serif";
    context.textBaseline = "middle";
    for (const tick of [0, geometry.top / 2, geometry.top]) {
      const y = Math.round(geometry.y(tick)) + 0.5;
      context.fillStyle = COLORS.grid;
      context.fillRect(PAD.left, y - 0.5, geometry.width - PAD.left - PAD.right, 1);
      context.fillStyle = COLORS.text;
      context.textAlign = "right";
      context.fillText(String(tick), PAD.left - 6, y);
    }
    series.forEach((second, index) => {
      if (second.orders <= 0) return;
      const left = geometry.x(index) + (geometry.slot - geometry.column) / 2;
      context.fillStyle = index === hovered ? COLORS.hover : COLORS.orders;
      columnPath(context, left, geometry.column, geometry.y(second.orders), geometry.y(0));
      context.fill();
    });
    context.strokeStyle = COLORS.capacity;
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.beginPath();
    series.forEach((second, index) => {
      const y = geometry.y(POOL * second.batches);
      if (index === 0) context.moveTo(geometry.x(index), y);
      else context.lineTo(geometry.x(index), y);
      context.lineTo(geometry.x(index) + geometry.slot, y);
    });
    context.stroke();
    if (series.length > 0) {
      context.fillStyle = COLORS.text;
      context.textBaseline = "alphabetic";
      context.textAlign = "left";
      context.fillText(clock(series[0].second), geometry.x(0), geometry.height - 4);
      context.textAlign = "right";
      context.fillText(clock(series[series.length - 1].second), geometry.width - PAD.right, geometry.height - 4);
    }
    const last = series[series.length - 1];
    canvas.setAttribute(
      "aria-label",
      last
        ? `Orders sent in each second up to ${clock(last.second)}; the last second had ${counted(last.orders, "order", "orders")} in ${counted(last.batches, "batch", "batches")}`
        : "No batches yet",
    );
  }

  function showTip(index) {
    hovered = index;
    draw();
    const second = series[index];
    if (!second) {
      tip.hidden = true;
      return;
    }
    const geometry = layout();
    tip.replaceChildren(
      element("strong", {}, counted(second.orders, "order", "orders")),
      element("span", {}, ` in ${counted(second.batches, "batch", "batches")}, pool ${POOL * second.batches}, at ${clock(second.second)}`),
    );
    tip.hidden = false;
    const left = Math.min(geometry.x(index) + geometry.slot + 6, geometry.width - tip.offsetWidth);
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${PAD.top}px`;
  }

  function hideTip() {
    hovered = null;
    tip.hidden = true;
    draw();
  }

  canvas.addEventListener("pointermove", (event) => {
    const geometry = layout();
    const index = Math.floor((event.offsetX - PAD.left) / geometry.slot) - (CHART_SECONDS - series.length);
    if (index >= 0 && index < series.length) showTip(index);
    else hideTip();
  });
  canvas.addEventListener("pointerleave", hideTip);
  canvas.addEventListener("focus", () => showTip(series.length - 1));
  canvas.addEventListener("blur", hideTip);
  canvas.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (step === undefined || hovered === null) return;
    event.preventDefault();
    showTip(Math.max(0, Math.min(series.length - 1, hovered + step)));
  });

  Feed.register({
    reset() {
      series = [];
      hovered = null;
      band.hidden = true;
    },
    update(reply) {
      band.hidden = !reply.throughput;
      if (!reply.throughput) return;
      renderFigures(reply.throughput.figures);
      series = reply.throughput.series.slice(-CHART_SECONDS);
      renderSecondsTable();
      if (hovered !== null && hovered < series.length) showTip(hovered);
      else draw();
    },
  });
})();
