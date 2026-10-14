"use strict";

// The #plans panel: the plan timeline, one row for each plan_change, oldest first and the newest kept in view.
(() => {
  const panel = document.getElementById("plans");
  const { element, clock } = Feed;
  const list = element("ol", { class: "timeline" });
  const empty = element("p", { class: "empty" }, "No plan yet. The first plan shows here as soon as the agent sets it.");
  panel.append(empty, list);

  function madeBy(rule) {
    if (rule === "model") return element("span", { class: "rule by-model" }, "model's choice");
    if (rule === null || rule === undefined) return element("span", { class: "rule" }, "–");
    return element("span", { class: "rule" }, `rule ${rule}`);
  }

  function row(event) {
    const data = event.data;
    return element(
      "li",
      { "data-seq": event.seq },
      element("span", { class: "time" }, clock(event.game_seconds)),
      element(
        "span",
        { class: "change" },
        element("span", { class: "plan old" }, data.old ?? "start"),
        element("span", { class: "arrow", "aria-label": "to" }, "→"),
        element("span", { class: "plan new" }, data.new ?? "–"),
      ),
      madeBy(data.rule),
    );
  }

  Feed.register({
    reset() {
      list.replaceChildren();
      empty.hidden = false;
    },
    update(reply) {
      const changes = reply.events.filter((event) => event.kind === "plan_change");
      if (changes.length === 0) return;
      // Follow the newest change, unless the reader has scrolled up to look at older ones.
      const following = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
      list.append(...changes.map(row));
      empty.hidden = true;
      if (following) list.scrollTop = list.scrollHeight;
    },
  });
})();
