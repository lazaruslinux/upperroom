# Channel points

Channel points are a light loyalty feature: viewers earn points just by watching
you live, and spend them on rewards you define. There is no store, no currency,
and no history to manage. Each account carries a single running balance, and a
redemption is announced in chat and on the overlay so you see it happen.

## Earning

While the stream is live, every viewer connected to the watch page earns **1
point per minute**. A viewer with several tabs or devices open still earns once
per minute, not once per tab. Points accrue only while you are actually live;
sitting in chat between streams earns nothing. The rate is fixed and not
configurable.

## Rewards (admin)

1. Open the admin dashboard (`/admin`) and find the **Rewards** panel, just below
   **Invites**.
2. Type a label (what the viewer gets) and a cost in points, then press **Add
   reward**. For example, "hydrate" for 50 or "streamer does ten pushups" for
   500.
3. Each reward shows in the list with a **Delete** button. Rewards are create or
   delete only: to change a cost, delete the reward and add it again.

Rewards are just prompts for you to act on. Redeeming one does not do anything
automatic; it spends the points and tells the room, and the rest is up to you.

## Redeeming (viewer)

On the watch page there is a small **pts** chip next to the chat box showing the
viewer's balance. Tapping it opens a panel listing the rewards and their costs.
A reward the viewer cannot yet afford is dimmed. Redeeming one deducts the cost
at once and posts a line in chat, for example "Sam redeemed hydrate (50)". The
same notice appears as a chip on the OBS overlay, so it shows on the broadcast
too.

The balance updates whenever a viewer redeems something or reopens the panel; it
does not tick up live on screen while they watch. A refresh or reopening the
rewards panel always shows the current total.
