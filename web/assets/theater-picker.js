/*
 * Choosing something to put on: the list of search results, and a show's
 * episodes behind it.
 *
 * One file because there are two of these. The dashboard has a panel and the
 * watch page has a modal for hosting from a phone, and until now each carried
 * its own copy of the same rendering, which meant every change to it had to be
 * made twice and stay identical by hand. The callers still own their own
 * fetching and their own message line; this owns what a row looks like and what
 * happens when a show is opened.
 *
 * Posters are fetched per row rather than sent with the results: a poster is a
 * picture on this server's disk after the first time anyone asks for it, and
 * pushing twenty-five of them through the projector socket on every search
 * would cost far more than the rows are worth.
 */

(function () {
  "use strict";

  // A few at a time. The first search of a library fetches every poster from
  // the projector, and asking for twenty-five at once is a burst that machine
  // has no reason to absorb.
  const POSTER_WORKERS = 3;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ---- posters ------------------------------------------------------------

  function posterFrame(jfId, queue) {
    const frame = el("div", "ts-poster");
    queue.push({ jfId, frame });
    return frame;
  }

  async function fillPoster(jfId, frame) {
    try {
      const reply = await fetch(
        `/api/admin/theater/art?jf_id=${encodeURIComponent(jfId)}`
      );
      if (!reply.ok) return;
      const { art } = await reply.json();
      if (!art || !frame.isConnected) return;
      const img = new Image();
      img.alt = "";
      img.src = art;
      frame.textContent = "";
      frame.appendChild(img);
    } catch {
      /* A row without a picture is a row without a picture. */
    }
  }

  function runPosterQueue(queue) {
    let next = 0;
    const worker = async () => {
      while (next < queue.length) {
        const job = queue[next++];
        await fillPoster(job.jfId, job.frame);
      }
    };
    for (let i = 0; i < Math.min(POSTER_WORKERS, queue.length); i++) worker();
  }

  // ---- rows ---------------------------------------------------------------

  function metaLine(bits) {
    return bits.filter(Boolean).join(" · ");
  }

  function playButton(item, opts) {
    const play = el("button", "chip-btn", "play");
    play.type = "button";
    play.addEventListener("click", async () => {
      play.disabled = true;
      const done = await opts.onPlay(item);
      // Left disabled on success: the title is going on, and a second press
      // would ask the projector to start it again.
      if (!done) play.disabled = false;
    });
    return play;
  }

  function titleRow(item, opts, queue, label, bits, action) {
    const row = el("div", "ts-row");
    row.appendChild(posterFrame(item.jf_id, queue));
    const text = el("div", "ts-label");
    text.appendChild(el("span", "ts-title", label));
    const meta = metaLine(bits);
    if (meta) text.appendChild(el("span", "ts-meta", meta));
    row.appendChild(text);
    row.appendChild(action);
    return row;
  }

  // ---- the episode picker -------------------------------------------------

  function seasonsOf(episodes) {
    const seen = [];
    episodes.forEach((e) => {
      const season = typeof e.season === "number" ? e.season : null;
      if (!seen.some((s) => s === season)) seen.push(season);
    });
    return seen;
  }

  function seasonName(season) {
    if (season === null) return "Other";
    return season === 0 ? "Specials" : `Season ${season}`;
  }

  function episodeLabel(item) {
    // The number leads, because that is how somebody looks for an episode.
    const number = typeof item.episode === "number" ? `${item.episode}. ` : "";
    return `${number}${item.title}`;
  }

  function renderEpisodes(container, show, episodes, opts) {
    container.textContent = "";
    const queue = [];

    const head = el("div", "ts-show");
    head.appendChild(posterFrame(show.jf_id, queue));
    const heading = el("div", "ts-label");
    heading.appendChild(el("span", "ts-title", show.title));
    heading.appendChild(el(
      "span", "ts-meta",
      metaLine([show.year, `${episodes.length} episode${episodes.length === 1 ? "" : "s"}`])
    ));
    head.appendChild(heading);
    const back = el("button", "chip-btn", "back");
    back.type = "button";
    back.addEventListener("click", opts.onBack);
    head.appendChild(back);
    container.appendChild(head);

    const seasons = seasonsOf(episodes);
    const list = el("div", "ts-episodes");
    let current = seasons[0];

    const chips = el("div", "ts-seasons");
    const paint = () => {
      [...chips.children].forEach((chip) => {
        chip.classList.toggle("selected", chip.dataset.season === String(current));
      });
      list.textContent = "";
      // One season at a time, so this is a handful of stills rather than the
      // whole run's worth. Its own queue, because a season switch throws the
      // last list away and its pending fetches with it.
      const stills = [];
      episodes
        .filter((e) => (typeof e.season === "number" ? e.season : null) === current)
        .forEach((item) => {
          list.appendChild(titleRow(
            item, opts, stills,
            episodeLabel(item),
            [item.runtime_min && `${item.runtime_min} min`,
             item.has_subtitles && "subtitles"],
            playButton(item, opts),
          ));
        });
      runPosterQueue(stills);
    };
    // One season, so there is nothing to choose between: the chips would be a
    // row of one saying what the heading already said.
    if (seasons.length > 1) {
      seasons.forEach((season) => {
        const chip = el("button", "lib-tab", seasonName(season));
        chip.type = "button";
        chip.dataset.season = String(season);
        chip.addEventListener("click", () => { current = season; paint(); });
        chips.appendChild(chip);
      });
      container.appendChild(chips);
    }
    container.appendChild(list);
    paint();
    runPosterQueue(queue);        // the show's own poster in the header
  }

  // ---- results ------------------------------------------------------------

  async function openShow(container, show, opts) {
    opts.message("");
    try {
      const reply = await fetch(
        `/api/admin/theater/episodes?series=${encodeURIComponent(show.jf_id)}`
      );
      const data = await reply.json().catch(() => ({}));
      if (!reply.ok) {
        opts.message(reply.status === 502
          ? "The projector is not connected."
          : (data.error || "Could not list the episodes."), false);
        return;
      }
      const episodes = data.episodes || [];
      if (!episodes.length) {
        opts.message("That show has no episodes in the library.", false);
        return;
      }
      renderEpisodes(container, show, episodes, opts);
    } catch {
      opts.message("Could not reach the server.", false);
    }
  }

  function render(container, results, opts) {
    container.textContent = "";
    const queue = [];
    results.forEach((item) => {
      let action;
      if (item.kind === "series") {
        // A show cannot be put on air; its episodes can.
        action = el("button", "chip-btn", "episodes");
        action.type = "button";
        action.addEventListener("click", () => openShow(container, item, opts));
      } else {
        action = playButton(item, opts);
      }
      container.appendChild(titleRow(
        item, opts, queue, item.title,
        [item.year,
         item.kind === "series" ? "show" : null,
         item.runtime_min && `${item.runtime_min} min`,
         item.has_subtitles && "subtitles"],
        action,
      ));
    });
    runPosterQueue(queue);
  }

  window.theaterPicker = { render };
})();
