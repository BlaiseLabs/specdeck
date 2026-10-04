Fernhill Wiki › Ops › Checkout & Money › Pricing & Discounts (v1)

Owner: Mark Doyle · Created: 11 Sep 2023 by Priya Shah · Last edited: 14 Apr 2026 by Jules K. · Status: ARCHIVED — superseded by the May 2026 pricing discussion, do not use · Views: 412

---

# Pricing & Discounts (v1)

This page explains how an order is priced at Fernhill Supply, from the basket subtotal to the final amount the customer pays. If you are quoting a customer, fixing an invoice, or setting up a product in the shop, this is the page to use. If something here doesn't match what the checkout does, the checkout is wrong — tell Mark.

**Contents**

1. Subtotal
2. Bulk discount
3. Clearance
4. Shipping
5. Tax
6. Rounding
7. Gift cards
8. Members / loyalty (draft)
9. Worked examples
10. FAQ
11. Page history

---

## 1. Subtotal

The subtotal is the price of each item times the quantity, added up across the whole basket. Prices on the site are the prices we charge; nothing is added at the line level.

- Price per line = unit price × quantity.
- Subtotal = sum of all lines.

There are no hidden handling fees. We tried a 50p "small order fee" in 2023 and took it out after two weeks because of complaints.

## 2. Bulk discount

Customers ordering in volume get **10% off the whole order** once the basket contains **50 or more units in total**.

- Count every unit in the basket, across all products. Fifty pens, or twenty pens plus thirty notebooks, both count as 50.
- Once the order reaches 50 units, 10% comes off the **entire subtotal**, not just some lines.
- 49 units gets nothing. 50 units gets the 10%.
- The discount is calculated on the subtotal and shown as a single "Bulk discount" line on the invoice.

Why "whole order"? Because the spreadsheet only has one discount cell per order and it keeps things simple for customers. (Priya, Sep 2023: "If they're buying 50 of anything they're a good customer, give them the 10.")

**Do not** give bulk pricing manually on orders under 50 units without checking with Priya first. Dan — this means the café samples too.

## 3. Clearance

Clearance items are discontinued patterns, damaged packaging, end-of-line colours and anything Mark has moved to the clearance bin.

- Clearance items are marked with the orange CLEARANCE tag in the shop.
- Clearance units **count toward the 50-unit bulk threshold** and receive the 10% bulk discount along with everything else in the order.
- Clearance items cannot be returned unless faulty.

Clearance prices are set by Mark, usually at or a little above cost. Ask before cutting a clearance price further.

## 4. Shipping

- Standard UK shipping is a flat **£4.95** per order.
- Shipping is **free on orders over £40**. The £40 is measured on the subtotal before any discount.
- "Over" means over: an order of exactly £40.00 pays shipping.
- Shipping is charged once per order regardless of the number of parcels.
- Collection from the unit is free but must be arranged with Mark in advance.

We do not ship outside the UK. If someone asks, say no politely and point them at the contact form in case that changes.

## 5. Tax

- Sales tax is charged at **6%**. *(Mark, Nov 2024: rate is 8% now per Odile — I'll update this section properly when I get a minute. Use 8 until then.)*
- Tax is charged on the **order subtotal** (before discount) **plus shipping**. The checkout adds tax as the last step and shows it as a separate line.
- Rate applied is the rate on the **invoice date**.
- The tax line on the invoice footer comes from the Word template on the shared drive ("Invoice_Template_FINAL_v3.docx"). If the rate changes, update the footer there.

## 6. Rounding

Round **each line** to the nearest penny before adding them together. Then round the discount, tax and total again at the end, to the nearest penny, using the spreadsheet's normal ROUND function.

If you're a penny out against Stripe, it's almost always because one tab of the spreadsheet rounds per line and another doesn't. Use the "Orders 2025" tab, which is the right one.

## 7. Gift cards

Gift cards are sold in £10, £25 and £50. For now they are set up in the shop as normal products and go through the checkout the same as anything else. *(Mark to check with Odile whether that's right for tax — Feb 2025.)*

## 8. Members / loyalty (DRAFT)

*Added by Jules, 14 Apr 2026. First draft for discussion — not live yet!*

Working name: **Fernhill Friends**.

- Members get **10% off** everything, every order.
- Applied **before** the bulk discount, so a member with a bulk order gets their 10% and then the bulk 10% on top.
- Includes clearance (members should feel looked after everywhere).
- Free to join from the account page.
- Launch: aiming for late May.

Questions for Priya: do we want a minimum order for the member discount? Do we want members to get free shipping at a lower threshold (£30?)?

## 9. Worked examples

**Example A — small order, no bulk**

- 3 × A5 Dot Grid Notebook (3-pack) @ £14.50 = £43.50
- Units: 3 (no bulk)
- Shipping: subtotal is over £40 → free
- Tax: 8% × £43.50 = £3.48
- Total: £46.98

**Example B — bulk across two products**

- 30 × Kraft C5 envelopes (pack of 25) @ £3.20 = £96.00
- 25 × Ballpoint pens (black, 10-pack) @ £2.40 = £60.00
- Units: 55 → bulk applies to the whole order
- Subtotal: £156.00
- Bulk discount: 10% × £156.00 = £15.60
- Shipping: free (over £40)
- Tax: 8% × £156.00 = £12.48
- Total: £156.00 − £15.60 + £12.48 = £152.88

**Example C — just under**

- 2 × Bamboo Desk Organiser @ £19.50 = £39.00
- Shipping: £4.95 (not over £40)
- Tax: 8% × (£39.00 + £4.95) = £3.52
- Total: £47.47

**Example D — clearance in a bulk order**

- 40 × Exercise book, old pattern (CLEARANCE) @ £0.60 = £24.00
- 12 × A4 ruled pad @ £2.10 = £25.20
- Units: 52 → 10% off everything
- Subtotal: £49.20 → bulk discount £4.92
- Shipping: free (over £40)
- Tax: 8% × £49.20 = £3.94
- Total: £48.22

## 10. FAQ

**Q: A customer has 48 units and wants to add 2 cheap pencils to get the bulk discount. Can they?**
Yes, that's fine and it's how the rule is meant to work.

**Q: Can I split a big order into two to get free shipping twice?**
Free shipping is per order, so splitting doesn't help the customer. Don't suggest it.

**Q: Do trade customers get anything extra?**
Not automatically. Dan can agree trade terms case by case with Priya's OK.

**Q: The customer says another shop gave them 15% for 100+. Can we match?**
No. Our bulk discount is 10% at 50 units. Be polite.

**Q: A customer was charged tax on shipping and is asking why.**
That's how we do it — tax is on the full amount including delivery.

**Q: Which date decides the tax rate?**
The invoice date.

## 11. Page history

| Date | Who | Change |
|---|---|---|
| 11 Sep 2023 | Priya | Created page |
| 02 Oct 2023 | Priya | Removed small order fee section |
| 19 Nov 2024 | Mark | Note on tax rate in section 5 |
| 06 Feb 2025 | Mark | Gift cards section; FAQ; examples tidied |
| 21 Mar 2025 | Dan | Added "trade customers" FAQ |
| 14 Apr 2026 | Jules | Members / loyalty draft section |
| 28 May 2026 | Mark | Page archived |

---

**Comments (3)**

> **Dan Whitlock** · 22 Mar 2025
> Can we make the bulk threshold 40? Brew Lab orders are usually 40-45 units.

> > **Priya Shah** · 22 Mar 2025
> > No.

> **Sam R.** · 9 Jun 2026
> Can this page be deleted or at least moved? It still comes up first when you search "discount" on the wiki and a new starter used it on a customer reply today.

> > **Mark Doyle** · 9 Jun 2026
> > I archived it, there's the status line up top. I don't want to delete it because Odile likes having the history. I'll see if I can stop it showing in search.
