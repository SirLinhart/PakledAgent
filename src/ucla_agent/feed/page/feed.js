"use strict";

// The feed page's poll loop and the helpers its panels share. Every panel registers an object with
// reset() and update(reply); each answer of GET /events is handed to every panel in turn.
const Feed = (() => {
  const POLL_MS = 1000;
  const panels = [];
  let lastSeq = 0;
  let match = null;
  let eventCount = 0;

  function register(panel) {
    panels.push(panel);
  }

  // An element with its attributes and children; text goes in as text, never as markup.
  function element(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value !== null && value !== undefined && value !== false) {
        node.setAttribute(name, value === true ? "" : String(value));
      }
    }
    for (const child of children) {
      if (child !== null && child !== undefined) {
        node.append(child instanceof Node ? child : String(child));
      }
    }
    return node;
  }

  // Game seconds as m:ss; a dash when unknown.
  function clock(seconds) {
    if (typeof seconds !== "number") return "–";
    const whole = Math.max(0, Math.floor(seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  }

  // A number with thousands separators and at most `digits` decimals; a dash when unknown.
  function number(value, digits = 0) {
    if (typeof value !== "number") return "–";
    return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function showStatus(reply) {
    const status = document.getElementById("status");
    status.classList.remove("unreachable");
    const time = new Date().toLocaleTimeString("en-GB");
    status.textContent = reply.match === null
      ? `Waiting for a match record to appear. Last answer at ${time}.`
      : `Following ${reply.match}: ${number(eventCount)} events, last answer at ${time}.`;
  }

  function showUnreachable(error) {
    const status = document.getElementById("status");
    status.classList.add("unreachable");
    status.textContent = `The feed server does not answer (${error.message}). Trying again every second.`;
  }

  async function poll() {
    try {
      const query = new URLSearchParams({ since: String(lastSeq) });
      if (match !== null) query.set("match", match);
      const response = await fetch(`/events?${query}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reply = await response.json();
      if (reply.reset) {
        eventCount = 0;
        panels.forEach((panel) => panel.reset());
      }
      match = reply.match;
      lastSeq = reply.last_seq;
      eventCount += reply.events.length;
      panels.forEach((panel) => panel.update(reply));
      showStatus(reply);
    } catch (error) {
      showUnreachable(error);
    } finally {
      setTimeout(poll, POLL_MS);
    }
  }

  return { register, element, clock, number, start: poll };
})();
