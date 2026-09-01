/*
 * Caption probe — paste into the DevTools console of any meeting tab.
 *
 * Answers the only question that cannot be answered from outside a real meeting:
 * what does this platform's caption markup actually look like? Everything else
 * about supporting a new platform is ordinary work; this is the part that has to
 * come from a live call.
 *
 * Use: join a meeting, turn captions on, say a few sentences, then paste this and
 * press Enter. It prints a report and copies it to the clipboard.
 *
 * It reads the page and nothing else. No network, no changes to the meeting.
 */
(() => {
  const out = [];
  const say = (s = "") => out.push(s);

  // Shadow-piercing search: parts of both Teams and Meet live in web components,
  // where a plain querySelectorAll finds nothing even though the element is
  // plainly there in the inspector.
  const deep = (sel, root = document, acc = [], d = 0) => {
    if (d > 12) return acc;
    try { acc.push(...root.querySelectorAll(sel)); } catch {}
    for (const el of root.querySelectorAll ? root.querySelectorAll("*") : []) {
      if (el.shadowRoot) deep(sel, el.shadowRoot, acc, d + 1);
    }
    return acc;
  };

  const desc = (n) => !n ? "(none)" :
    "<" + n.tagName.toLowerCase()
    + (n.id ? "#" + n.id : "")
    + [...(n.classList || [])].slice(0, 3).map(c => "." + c).join("")
    + [...n.attributes].filter(a => /^(data-|aria-|jsname|role)/.test(a.name))
        .slice(0, 5).map(a => ` ${a.name}="${a.value.slice(0, 40)}"`).join("")
    + ">";

  const chain = (n, up = 6) => {
    const parts = [];
    for (let i = 0; i < up && n; i++, n = n.parentElement) parts.unshift(desc(n));
    return parts.join(" > ");
  };

  say(`CAPTION PROBE — ${location.hostname}`);
  say(`when: ${new Date().toISOString()}`);
  say("");

  // 1. Anything that names itself a caption.
  say("--- elements whose attributes mention captions ---");
  const named = deep("*").filter(n =>
    [...n.attributes].some(a => /caption|subtitle|transcript/i.test(a.name + " " + a.value)));
  if (!named.length) say("  (none — the markup does not label itself)");
  for (const n of named.slice(0, 25)) {
    const own = [...n.childNodes].filter(c => c.nodeType === 3)
      .map(c => c.textContent.trim()).join(" ").slice(0, 60);
    say(`  ${desc(n)}${own ? `   "${own}"` : ""}`);
  }
  if (named.length > 25) say(`  ... and ${named.length - 25} more`);
  say("");

  // 2. Where the words you said actually live. This is the important one: it
  //    finds the caption text by content rather than by a guess at a selector.
  const phrase = (prompt("Type a few words you just said out loud, as they "
                       + "appeared in the captions:") || "").trim();
  if (phrase) {
    say(`--- elements containing ${JSON.stringify(phrase)} ---`);
    const hit = deep("*").filter(n =>
      (n.textContent || "").includes(phrase) &&
      ![...n.children].some(c => (c.textContent || "").includes(phrase)));  // innermost only
    if (!hit.length) say("  (not found — try a shorter phrase, exactly as captioned)");
    for (const n of hit.slice(0, 5)) {
      say(`  innermost: ${desc(n)}`);
      say(`  ancestry:  ${chain(n)}`);
      /*
       * Where one line begins, and where the speaker's name sits inside it.
       *
       * The first version of this guessed by looking for "author", "speaker" or
       * "name" anywhere in an element's attributes -- and reported a "Jump to
       * bottom" button, because the attribute called `jsname` contains "name".
       * Guessing by naming was the wrong idea anyway on a platform whose class
       * names are generated. This dumps the structure instead and lets a person
       * read it.
       */
      let line = n;
      for (let i = 0; i < 4 && line.parentElement; i++) {
        line = line.parentElement;
        // A line is the smallest ancestor holding more than just this text --
        // usually the text plus whatever names the person who said it.
        if (line.children.length > 1) break;
      }
      say(`  the line:  ${desc(line)}`);
      say(`  its full contents:`);
      const tree = (nd, d = 1) => {
        if (d > 5) return;
        const own = [...nd.childNodes].filter(x => x.nodeType === 3)
          .map(x => x.textContent.trim()).filter(Boolean).join(" ");
        say("    " + "  ".repeat(d) + desc(nd) + (own ? `   "${own.slice(0, 60)}"` : ""));
        for (const ch of nd.children) tree(ch, d + 1);
      };
      tree(line);
      say("");
    }
  }

  // 3. Every caption line currently on screen, side by side.
  //
  //    One line tells you almost nothing: what matters is whether two people's
  //    turns are separate blocks or one, and whether the speaker's name repeats.
  //    Say something, have somebody else say something, then run this.
  const region = deep('[role="region"]').find(r =>
    /caption|sous-titre|untertitel|subtitul|字幕|kapsiyon/i.test(r.getAttribute("aria-label") || ""))
    || deep('[jsname="dsyhDe"]')[0];
  if (region) {
    say(`--- the caption area: ${desc(region)} ---`);
    say(`  ${region.children.length} block(s) inside it right now:`);
    for (const block of [...region.children].slice(0, 6)) {
      say(`    ${desc(block)}`);
      for (const kid of [...block.children].slice(0, 6)) {
        say(`      ${desc(kid)}   "${(kid.textContent || "").trim().slice(0, 50)}"`);
      }
    }
  } else {
    say("--- could not find a caption region by role/aria-label ---");
  }

  const report = out.join("\n");
  console.log(report);
  navigator.clipboard.writeText(report).then(
    () => console.log("%c[probe] copied to clipboard — paste it back", "color:#34d399"),
    () => console.log("%c[probe] select the text above and copy it", "color:#f59e0b"));
})();
