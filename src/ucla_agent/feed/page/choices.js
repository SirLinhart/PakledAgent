"use strict";

// The #choices panel: every request to a model, newest first, each one opening onto what was sent and what came back.
(() => {
  const panel = document.getElementById("choices");
  const { element, clock, number } = Feed;
  const CHOICE_KINDS = new Set(["decision", "text_decision"]);
  const USED = "used";
  // The parts of a call's io, in the order a reader follows the exchange.
  const IO_PARTS = [
    ["system", "System message"],
    ["user", "User message"],
    ["response_format", "Response format"],
    ["params", "Parameters"],
    ["raw_output", "Raw output"],
    ["reasoning_text", "Reasoning text"],
  ];
  const count = element("span", { class: "count" });
  const list = element("div", { class: "choice-list" });
  const empty = element("p", { class: "empty" }, "No model choice yet. Each request to a model shows here as it is answered.");
  panel.querySelector("h2").append(" ", count);
  panel.append(empty, list);
  let shown = 0;

  // The option ids a request named: the bracketed list that ends its summary.
  function optionsOf(summary) {
    const found = /\[([^\]]*)\]\s*$/.exec(summary || "");
    return found ? found[1].split(",").map((option) => option.trim()).filter(Boolean) : [];
  }

  function when(event) {
    if (typeof event.game_seconds === "number") return clock(event.game_seconds);
    return String(event.data.state_version ?? event.data.state_tag ?? "–");
  }

  function choiceLine(data) {
    const options = optionsOf(data.request_summary);
    const answered = data.answer ?? null;
    const chips = options.map((option) =>
      element("span", { class: option === answered ? "option chosen" : "option" }, option),
    );
    if (answered !== null && !options.includes(answered)) chips.push(element("span", { class: "option off-list" }, answered));
    if (answered === null) chips.push(element("span", { class: "no-answer" }, "no answer"));
    return element("span", { class: "options" }, ...chips);
  }

  function textLine(data) {
    const written = data.text ?? (data.io && data.io.raw_output) ?? null;
    return element("span", { class: "written" }, written === null ? "no text" : written);
  }

  function outcome(data) {
    const used = data.outcome === USED;
    const text = used ? USED : [data.outcome, data.reason].filter((part, index, parts) => part && parts.indexOf(part) === index).join(": ");
    return element("span", { class: used ? "outcome used" : "outcome fell-back" }, text);
  }

  function ioBlock(io) {
    if (!io) return element("p", { class: "empty" }, "Nothing was sent to a model for this choice, so there is no exchange to show.");
    return element(
      "dl",
      { class: "io" },
      ...IO_PARTS.flatMap(([key, label]) => {
        const value = io[key];
        const text = value === null || value === undefined ? "(none)" : typeof value === "string" ? value : JSON.stringify(value, null, 2);
        return [element("dt", {}, label), element("dd", {}, element("pre", {}, text))];
      }),
    );
  }

  function choice(event) {
    const data = event.data;
    const toggle = element(
      "button",
      { type: "button", class: "choice-row", "aria-expanded": "false" },
      element("span", { class: "time" }, when(event)),
      element("span", { class: "source" }, data.source ?? "–"),
      event.kind === "decision" ? choiceLine(data) : textLine(data),
      element("span", { class: "latency" }, typeof data.latency_ms === "number" ? `${number(data.latency_ms)} ms` : "–"),
      outcome(data),
      data.model_reason ? element("span", { class: "model-reason" }, data.model_reason) : null,
    );
    const details = element("div", { class: "choice-io", hidden: true }, ioBlock(data.io));
    toggle.addEventListener("click", () => {
      const opening = details.hidden;
      details.hidden = !opening;
      toggle.setAttribute("aria-expanded", String(opening));
    });
    return element("article", { class: "choice", "data-seq": event.seq }, toggle, details);
  }

  Feed.register({
    reset() {
      list.replaceChildren();
      shown = 0;
      count.textContent = "";
      empty.hidden = false;
    },
    update(reply) {
      const choices = reply.events.filter((event) => CHOICE_KINDS.has(event.kind));
      for (const event of choices) list.prepend(choice(event));
      shown += choices.length;
      count.textContent = shown ? `(${number(shown)})` : "";
      empty.hidden = shown > 0;
    },
  });
})();
