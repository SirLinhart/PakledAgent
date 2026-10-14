"use strict";

// The #activity panel: every other event of the match in record order, one line each, with a filter per kind.
(() => {
  const panel = document.getElementById("activity");
  const { element, clock, number } = Feed;
  // Shown in their own panels.
  const ELSEWHERE = new Set(["decision", "text_decision", "plan_change"]);
  // Five a second in Red Alert: left out until the reader asks for them.
  const HIDDEN_AT_FIRST = new Set(["action", "action_result"]);
  const GENERIC_FIELDS = 4;
  const LABEL_LENGTH = 160;

  const filters = element("div", { id: "activity-filters", role: "group", "aria-label": "Kinds of event shown" });
  const list = element("ol", { class: "activity-list" });
  const empty = element("p", { class: "empty" }, "No event yet. Raids, errors, refinery and placement choices and the rest show here as they happen.");
  panel.append(filters, empty, list);
  const kinds = new Map();

  const cell = (value) => (Array.isArray(value) ? `(${value.join(", ")})` : value ?? "–");
  const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
  const words = (text) => String(text ?? "–").replaceAll("_", " ");

  function batchLabel(data) {
    if (!data.action || typeof data.action !== "object") return "Wait: no orders sent";
    const orders = Array.isArray(data.action.orders) ? data.action.orders : [];
    const byCommand = new Map();
    for (const order of orders) byCommand.set(order.cmd, (byCommand.get(order.cmd) || 0) + 1);
    const commands = typeof data.command_count === "number" ? `, ${plural(data.command_count, "engine command", "engine commands")}` : "";
    const parts = [...byCommand].map(([command, count]) => `${command} ${count}`).join(", ");
    return `Batch of ${plural(orders.length, "order", "orders")}${commands}: ${parts}`;
  }

  function answerLabel(data) {
    const result = data.result || {};
    if (data.accepted === false) return `Batch refused: ${words(result.error)}${result.detail ? ` (${result.detail})` : ""}`;
    return `Batch accepted: ${number(result.accepted_orders)} orders taken, ${number(result.rejected_orders)} rejected`;
  }

  function raidLabel(data) {
    if (data.phase === "start") {
      return `Raid sets out at ${data.target_type ?? "a target"} ${data.target ?? ""}: ${plural(data.group_size, "unit", "units")} worth ${number(data.value)}`;
    }
    if (data.phase === "end") {
      return `Raid ends (${words(data.reason)}): ${plural(data.kills ?? 0, "kill", "kills")}, ${plural(data.units_lost ?? 0, "unit", "units")} lost worth ${number(data.value_lost)}`;
    }
    return genericLabel(data);
  }

  // A one-line label for each kind the agent writes; any other kind is labeled by its first fields.
  const LABELS = {
    action: batchLabel,
    action_result: answerLabel,
    raid: raidLabel,
    refinery_field: (data) => `Refinery at ${cell(data.cell)} serves the field at ${cell(data.field_center)} (${words(data.rule)}, ${number(data.field_cells)} cells)`,
    creep_placement: (data) => `${data.building} placed at ${cell(data.cell)} toward the ore at ${cell(data.ore_cell)}: ${number(data.distance_before_cells, 1)} to ${number(data.distance_after_cells, 1)} cells`,
    opening_reach: (data) => `Opening refinery reach ${cell(data.refinery_reaches)}, field at ${cell(data.field_center)}, ${number(data.distance_cells, 1)} cells away`,
    plan_rule_blocked: (data) => `Rule ${data.rule} held back (${words(data.reason)}); ${data.plan} stays by ${data.plan_rule}`,
    harvesters_parked: (data) => `${plural(Array.isArray(data.harvesters) ? data.harvesters.length : data.harvesters, "harvester", "harvesters")} parked at ${cell(data.cell)} (${words(data.reason)})`,
    error: (data) => `${data.type === "unhandled" ? "Unhandled error" : "Handled error"} in ${data.where}: ${data.exception}: ${data.message}`,
    fallback: (data) => `${data.intended_source} not used (${words(data.reason)}); ${data.used} decided`,
    timing: (data) => `Ticks ${data.from_tick} to ${data.to_tick}: compute mean ${number(data.compute_ms && data.compute_ms.mean, 1)} ms, income ${number(data.income_per_s, 1)}/s`,
    ledger_summary: (data) => `Ledger: ${number(data.orders_sent)} orders sent, ${number(data.orders_lost)} lost`,
    game_config: () => "Game reference received",
    result: (data) => `Result: ${words((data.result || {}).termination_reason)}`,
    match_end: (data) => `Record closed without a result (${words(data.reason)})`,
  };

  function genericLabel(data) {
    return Object.entries(data)
      .slice(0, GENERIC_FIELDS)
      .map(([key, value]) => `${key} ${typeof value === "object" && value !== null ? JSON.stringify(value) : value}`)
      .join(", ");
  }

  function label(event) {
    const made = (LABELS[event.kind] || genericLabel)(event.data);
    return made.length > LABEL_LENGTH ? `${made.slice(0, LABEL_LENGTH - 1)}…` : made;
  }

  function filterFor(kind) {
    const box = element("input", { type: "checkbox" });
    box.checked = !HIDDEN_AT_FIRST.has(kind);
    const tally = element("span", { class: "tally" });
    const entry = { box, tally, count: 0, label: element("label", {}, box, ` ${words(kind)} `, tally) };
    box.addEventListener("change", () => {
      for (const row of list.querySelectorAll(`li[data-kind="${CSS.escape(kind)}"]`)) row.hidden = !box.checked;
    });
    const placed = [...kinds.keys()].filter((known) => known < kind).length;
    filters.insertBefore(entry.label, filters.children[placed] || null);
    kinds.set(kind, entry);
    return entry;
  }

  function row(event) {
    const data = event.data;
    const toggle = element(
      "button",
      { type: "button", class: "activity-row", "aria-expanded": "false" },
      element("span", { class: "time" }, clock(event.game_seconds)),
      element("span", { class: "kind" }, words(event.kind)),
      element("span", { class: "label" }, label(event)),
    );
    const details = element("pre", { class: "activity-data", hidden: true });
    toggle.addEventListener("click", () => {
      if (!details.textContent) details.textContent = JSON.stringify(data, null, 2);
      details.hidden = !details.hidden;
      toggle.setAttribute("aria-expanded", String(!details.hidden));
    });
    const classes = event.kind === "error" && data.type === "unhandled" ? "unhandled" : null;
    return element("li", { "data-kind": event.kind, "data-seq": event.seq, class: classes }, toggle, details);
  }

  Feed.register({
    reset() {
      list.replaceChildren();
      filters.replaceChildren();
      kinds.clear();
      empty.hidden = false;
    },
    update(reply) {
      const shown = reply.events.filter((event) => !ELSEWHERE.has(event.kind));
      if (shown.length === 0) return;
      // Follow the newest event, unless the reader has scrolled up to look at older ones.
      const following = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
      const rows = shown.map((event) => {
        const entry = kinds.get(event.kind) || filterFor(event.kind);
        entry.count += 1;
        const made = row(event);
        made.hidden = !entry.box.checked;
        return made;
      });
      list.append(...rows);
      for (const entry of kinds.values()) entry.tally.textContent = `(${number(entry.count)})`;
      empty.hidden = true;
      if (following) list.scrollTop = list.scrollHeight;
    },
  });
})();
