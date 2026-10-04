**Ticket #4471** — "charged tax on a gift card??"
Status: Solved  |  Priority: Normal  |  Assignee: Sam R.  |  Channel: email
Order: #10517 (placed 2026-05-21)
Tags: billing, tax, gift-card, refund

---

**From:** Helen Okafor
**Sent:** Fri 22 May 2026, 08:47

Hi there,

I placed an order yesterday (#10517) and I've just been looking at the receipt and I think something's wrong. I bought a gift card for my sister's birthday along with a few things for myself and it looks like you've charged me tax on the gift card as well? I'm fairly sure that's not right, I've never been charged tax on a gift card anywhere else.

Here's what the receipt says, copied from the email:

```
A5 Dot Grid Notebook (3-pack)     x2    14.50    29.00
Bamboo Desk Organiser             x1    32.00    32.00
Fernhill Gift Card - 50           x1    50.00    50.00
-----------------------------------------------------
Subtotal                                        111.00
Tax                                               8.88
Shipping                                          FREE
Total                                           119.88
```

8.88 is 8% of the whole 111, isn't it? Could someone have a look please.

Thanks,
Helen

---

**From:** Sam R. (Fernhill Support)
**Sent:** Fri 22 May 2026, 10:12

Hi Helen,

Thanks for getting in touch and sorry for the confusion!

I've had a look at order #10517. Tax is calculated by our checkout across the items in the order, so the 8.88 does look like it's been worked out correctly on the subtotal. Gift cards are sold as products on our site so I believe they're treated the same as the other items, but I completely understand it's not what you were expecting.

Is there anything else I can help with?

Best wishes,
Sam
Fernhill Supply — Customer Support

---

**From:** Helen Okafor
**Sent:** Fri 22 May 2026, 11:30

Hi Sam,

Sorry but I don't think that's right. A gift card isn't really a "product", it's just money in a different form — when my sister spends it she'll pay tax on whatever she buys with it, so I'd be paying tax twice on the same 50 pounds. I used to work in retail and we never, ever taxed gift cards at the till.

Could you check with someone? It's only 4 pounds but it's the principle.

Helen

---

**From:** Sam R. (Fernhill Support)
**Sent:** Fri 22 May 2026, 11:41

Hi Helen,

That's a fair point and I don't want to give you the wrong answer — I'm checking with our finance team now and will come back to you today.

Sam

---

**From:** Sam R. (Fernhill Support)
**Sent:** Fri 22 May 2026, 15:58

Hi Helen,

You were right, and I'm sorry I got that wrong this morning. Gift cards shouldn't have had tax on them. I've refunded the 4.00 that was charged on the gift card back to your original payment method — it should show in 3–5 working days.

Your notebooks and organiser were taxed correctly (8% on 61.00 = 4.88), so your corrected total is 115.88.

Thanks for being patient with us, and I hope your sister enjoys the card!

Best,
Sam

---

**From:** Helen Okafor
**Sent:** Fri 22 May 2026, 16:20

Brilliant, thank you Sam. Appreciate you chasing it.

---

> **INTERNAL NOTE** — Sam R., 22 May 2026 16:05 (not visible to customer)
>
> Checked with Odile (cc Mark). Customer is correct — gift cards are NOT taxable, full stop. It's stored value; tax gets collected when the card is redeemed against actual goods, not when the card is bought. Odile says this has always been the case and the checkout is just wrong. Refunded 4.00 via Stripe (partial refund on ch_...9f2a).
>
> Mark — this is a checkout bug, not a one-off. Any order with a gift card line is currently getting tax on it. Can this go on the list for the pricing rebuild? I've seen at least two other orders this month with gift cards in, haven't checked them yet. Also my first reply was wrong — I'll do a macro for gift card tax questions so nobody else repeats it.
>
> > **Mark** (16:31): yep adding it. thanks for catching. will pull the other gift card orders monday and see what we owe people
