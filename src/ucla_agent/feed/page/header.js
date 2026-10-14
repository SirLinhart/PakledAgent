"use strict";

// The #header bar: where the match stands, from the header fields of every reply.
(() => {
  const strip = document.getElementById("header");
  const { element, clock, number } = Feed;
  const OUTCOMES = { win: "Won", loss: "Lost", draw: "Draw", no_result: "No result" };

  function figure(value, label, kind = "") {
    return element(
      "div",
      { class: `figure ${kind}`.trim() },
      element("span", { class: "value" }, value),
      element("span", { class: "label" }, label),
    );
  }

  function signed(value) {
    if (typeof value !== "number") return "–";
    return `${value > 0 ? "+" : value < 0 ? "−" : ""}${number(Math.abs(value))}`;
  }

  function balance(value) {
    const node = figure(signed(value), "trade balance");
    if (typeof value === "number" && value !== 0) node.classList.add(value > 0 ? "gain" : "loss");
    return node;
  }

  // Our army value against the enemy's seen, with the share of the two that is ours drawn between them.
  function armies(ours, theirs) {
    const known = typeof ours === "number" && typeof theirs === "number" && ours + theirs > 0;
    const share = known ? (100 * ours) / (ours + theirs) : 50;
    const label = `Our army ${number(ours)} against ${number(theirs)} of the enemy's seen`;
    const bar = element("span", { class: "bar", role: "img", "aria-label": label }, element("span", { class: "ours" }));
    bar.firstChild.style.width = `${share}%`;
    return element(
      "div",
      { class: "figure armies" },
      element("span", { class: "value" }, element("span", { class: "gain" }, number(ours)), bar, element("span", { class: "loss" }, number(theirs))),
      element("span", { class: "label" }, "our army against the enemy army seen"),
    );
  }

  function result(header) {
    if (!header.result) return null;
    const reason = header.result.reason ? String(header.result.reason).replaceAll("_", " ") : "no reason given";
    return figure(OUTCOMES[header.result.outcome] || header.result.outcome, reason, `result ${header.result.outcome}`);
  }

  function redAlertFigures(header) {
    const left = typeof header.remaining_seconds === "number" ? `${clock(header.remaining_seconds)} left` : "time left unknown";
    return [
      figure(clock(header.game_seconds), left, "clock"),
      figure(number(header.cash), "cash"),
      balance(header.trade_balance),
      armies(header.army_value, header.enemy_army_value),
      figure(number(header.harvesters), "harvesters"),
      figure(typeof header.explored_percent === "number" ? `${number(header.explored_percent, 1)}%` : "–", "map explored"),
      figure(header.plan || "–", "plan"),
    ];
  }

  function render(header) {
    if (!header.game_type) {
      strip.replaceChildren();
      return;
    }
    const figures = header.game_type === "red_alert" ? redAlertFigures(header) : [figure(header.game_type, "game")];
    strip.replaceChildren(...figures, ...[result(header)].filter(Boolean));
  }

  Feed.register({
    reset() {
      strip.replaceChildren();
    },
    update(reply) {
      render(reply.header || {});
    },
  });
})();
