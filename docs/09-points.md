# Channel points

Channel points are a light loyalty feature: viewers earn points just by watching
you live, and spend them on one thing, highlighting a short message on stream.
There is no store, no catalog, and no history to manage. Each account carries a
single running balance, and a highlight is announced in chat and on the overlay
so you see it happen.

## Earning

While the stream is live, every viewer connected to the watch page earns **1
point per minute**. A viewer with several tabs or devices open still earns once
per minute, not once per tab. Points accrue only while you are actually live;
sitting in chat between streams earns nothing. The rate is fixed and not
configurable.

## Highlighting a message (viewer)

On the watch page there is a small **pts** chip next to the chat box showing the
viewer's balance. Tapping it opens a small composer: the current balance, the
cost, and a single-line box for the message to highlight. The cost is a fixed
**50 points**, roughly fifty minutes of watching. The send button stays disabled
until the balance covers the cost and there is something to say.

Sending spends the points at once and posts the message as a highlighted line in
chat, framed by the channel's accent color. The same message appears as a
prominent chip on the OBS overlay, with a soft chime, so it shows on the
broadcast too. The message obeys the same length limit as normal chat and is checked
against the same word filter, and a viewer who is timed out or banned from chat
cannot highlight either.

There is no admin configuration. The cost and the earn rate are both fixed in the
code, and there is nothing to set up.

## Balances

The balance updates whenever a viewer highlights a message or reopens the
composer; it does not tick up live on screen while they watch. A refresh or
reopening the composer always shows the current total.
