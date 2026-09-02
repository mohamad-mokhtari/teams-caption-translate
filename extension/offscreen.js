/*
 * Speaks translated caption rows, out of reach of the meeting tab's mute.
 *
 * The queue policy lives here rather than in the content script because this is
 * where the utterances actually complete: only this document knows when one has
 * finished, and guessing from the other side drifts.
 */
"use strict";

/** How far behind the meeting the voice may fall before it stops trying. */
const MAX_QUEUE = 3;

let queued = 0;

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.target !== "offscreen") return;

  if (msg.type === "stop") {
    if (queued) speechSynthesis.cancel();
    queued = 0;
    return;
  }
  if (msg.type !== "speak" || !msg.text) return;

  /*
   * Staying current beats saying everything.
   *
   * Speech takes as long as the sentence does, so a queue can only ever fall
   * further behind a conversation that keeps going. Past a few rows the right
   * thing is to drop what is stale and follow the meeting, not to recite a
   * backlog nobody can still place.
   */
  if (queued >= MAX_QUEUE) {
    speechSynthesis.cancel();
    queued = 0;
  }

  const u = new SpeechSynthesisUtterance(msg.text);
  const voice = speechSynthesis.getVoices().find(v => v.name === msg.voice);
  if (voice) { u.voice = voice; u.lang = voice.lang; }
  u.rate = msg.rate || 1;

  queued++;
  const done = () => { queued = Math.max(0, queued - 1); };
  u.onend = done;
  u.onerror = done;
  speechSynthesis.speak(u);
});
