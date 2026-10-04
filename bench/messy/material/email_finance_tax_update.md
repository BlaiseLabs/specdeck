**From:** Odile Marchetti <odile@marchetti-bookkeeping.co>
**To:** Priya Shah <priya@fernhillsupply.com>; Mark Doyle <mark@fernhillsupply.com>
**Cc:** Dan Whitlock <dan@fernhillsupply.com>
**Date:** Wed 20 May 2026, 18:42
**Subject:** RE: FW: tax rate change + the rounding question (sorry, long one)

Hi all,

As promised at the end of last week, here's the tax rate thing written up so nobody has to remember what I said on the phone.

The rate is going up. From 1 July 2026 it's 10%. Until then it stays at 8%, which is what we've been charging since the last change.

The bit that matters for the new checkout: it's the date the order is *placed* that decides which rate applies. So an order placed on 30 June gets 8% even if Mark doesn't ship it until the 3rd of July, and anything placed on 1 July or after gets 10%. Not the ship date, not the invoice date, not the payment-cleared date — the order date. I know the spreadsheet currently stamps the invoice date on things, so please don't use that column for this.

(Before anyone digs up the old invoice template — yes, it still says 6% in the footer. That was the rate when Fernhill first registered back in 2023 and it hasn't applied for ages. Priya, I think the Word template on the shared drive is the culprit, can we just delete it.)

And a reminder since the audit last year: shipping is never taxed, so please make sure the new system doesn't quietly start putting tax on the delivery charge again.

Mark also asked me about rounding a couple of weeks ago, my reply to him is further down this chain — I've forwarded the whole thing so it's all in one place.

Separately, Priya, my April invoice is attached, and I'll need the Q2 bank statements by the 10th of July at the latest. The Hartwell's credit note still hasn't come through either, I'll chase them.

Thanks,
Odile

--
Odile Marchetti
Marchetti Bookkeeping
Mon/Wed/Thu only

---

**From:** Priya Shah
**Sent:** Wed 20 May 2026, 19:05
**To:** Odile Marchetti; Mark Doyle
**Cc:** Dan Whitlock
**Subject:** RE: FW: tax rate change + the rounding question (sorry, long one)

Thank you!! This is perfect. Deleting the template now.

Mark can you make sure whoever builds the checkout gets this whole email, including the bottom bit.

P

Sent from my iPhone

---

**From:** Mark Doyle
**Sent:** Wed 20 May 2026, 21:17
**To:** Priya Shah; Odile Marchetti
**Cc:** Dan Whitlock
**Subject:** RE: FW: tax rate change + the rounding question (sorry, long one)

will do. odile one q — the order that comes in at like 11:58pm on the 30th of june, is that the 30th? we're on uk time for the order timestamps i think

---

**From:** Odile Marchetti
**Sent:** Thu 21 May 2026, 09:03
**To:** Mark Doyle; Priya Shah
**Cc:** Dan Whitlock
**Subject:** RE: FW: tax rate change + the rounding question (sorry, long one)

Yes, if the order says 30 June it's 30 June. Use whatever date the shop records on the order.

O.

---

---------- Forwarded message ----------
**From:** Odile Marchetti
**Date:** Tue 5 May 2026, 14:20
**Subject:** RE: rounding??
**To:** Mark Doyle

Hi Mark,

Ha, yes, I saw. The penny differences are because the spreadsheet rounds at different points depending on which tab you're on, and on one of them it's rounding every single line before adding up and on another it's not rounding at all until the end and then Excel's doing banker's rounding on top, which is how you've ended up a penny out against Stripe three times this month.

What I need is simple: work out the discount and round it to the cent, half up (so .005 goes up, not to the nearest even). Then work out the tax and round that to the cent, also half up, separately — don't fold it into the discount rounding or round the two together. Then the total is just the subtotal, minus the discount, plus the tax, plus shipping. Nothing else, no rounding again at the end, it's already in pennies by then.

If the system does that, it'll match what I reconcile against and we're done with the Friday penny hunt.

Odile

> ---------- Original message ----------
> **From:** Mark Doyle
> **Date:** Mon 4 May 2026, 17:51
> **Subject:** rounding??
> **To:** Odile Marchetti
>
> hi odile, sorry to bother you on a monday. the totals on the invoices keep being a penny off from what stripe says, three times this month. Priya says we're rebuilding the checkout pricing anyway so whats the right way to round things so it matches what you need? I've got no idea which tab of the spreadsheet is "right"
>
> also unrelated, if we end up having to buy a new label printer does that count as equipment or consumables. its about 300
>
> thanks
> Mark

> > **Odile** (on the printer, from a separate reply on 5 May): Equipment. Keep the receipt.
