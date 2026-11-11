# BasketBuddy evaluation results

Run: 11 Nov 2026 20:10 · LLM: groq · openai/gpt-oss-120b (answers; decomposition uses the fast model) · cache reads: off (--fresh) · SIM_THRESHOLD 0.7

## 1. End-to-end questions

- Fallback behaviour as expected: **15/16**
- Evidence present (keyword check): **12/13**
- Faithful (citations ⊆ retrieved, cited prices match metadata): **12/12** answered questions
- Latency of answered questions: median **2.1 s**, max 4.0 s

Manual score: 1 = wrong or irrelevant, 2 = partly right, 3 = correct and relevant. **Fill in by hand.**

| ID | Type | Question | Top-3 retrieved | Evidence? | Answer | Faithful? | Fallback exp → act | Category check | Latency | LLM calls | Manual score (1–3) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | factual | What is the price of Fortune sunflower refined oil 1 L? | Sun Lite - Sunflower Refined Oil; Sun Lite - Sunflower Refined Oil; Refined Oil - Rice Bran | Y | - Sun Lite – Sunflower Refined Oil – ₹171 (Rating 4.1/5) [P15438] - Sun Lite – Sunflower Refined Oil – ₹866 (Rating 4.1/5) [P6749] The dataset does not record the pack size for these variants. | Y | N → N |  | 2.1 s | 2 |  |
| Q2 | comparison | Compare Aashirvaad and Pillsbury whole wheat atta 5 kg on price and rating. | Select Atta/Godihittu; Atta/Godihittu - Multigrain; Sugar Release Control Atta/Godihittu | Y | - **Aashirvaad whole wheat atta** – Price ₹406, Rating 4.1/5 [P23556] - **Pillsbury whole wheat atta** – No whole‑wheat atta product is listed for Pillsbury in the data. The dataset does not record pack sizes for these items. | Y | N → N |  | 1.9 s | 2 |  |
| Q3 | multi-part | Give me everything I need for dal makhani for 4 people, budget mode, I already have salt … | Urad - Black, Whole/Sabut; Red Rajma/Capparadavare; Fresh Cream | Y | This cart was built from the **ingredients of the recipe ‘Creamy Dal Makhani’ from the Archana's Kitchen recipe dataset (serves 4)**, using the Budget selection mode. It includes essentials like Black Urad Dal at **₹31** [P16295] and Fresh Cream at **₹47** [P… | Y | N → N |  | 2.5 s | 1 |  |
| Q4 | pasted recipe | Quick poha: 2 cups poha, 1 onion chopped, 1/2 tsp mustard seeds, 8-10 curry leaves, 1 gre… | Organic Poha (Flattened Rice/Aval/Atukulu); Onion; Organic Mustard/Sasive Seeds - Whole, Big | Y | Here’s a quick‑shopping list created from the ingredients extracted from the recipe the user pasted. It includes key items such as Organic Poha at ₹61.88 [P20102], Fresh Onion at ₹28.71 [P11011] and Lemon Juice Concentrate at ₹285 [P8863]. The cart total come… | Y | N → N |  | 4.0 s | 2 |  |
| Q5 | hinglish | jeera aur haldi kitne ka hai? | Organic Cumin Seeds/Jeera/Jeerige; Organic Turmeric Powder/Haldi/Arisina Pudi; Organic Cumin Seed Powder | Y | **Jeera (Cumin)** - Organic Cumin Seeds/Jeera – ₹169, not rated [P22635] - Organic Cumin Seed Powder – ₹56, rating 4/5 [P10674] - Jeera/Cumin Powder – ₹32, rating 5/5 [P4842] *The dataset does not record pack sizes, so these are different price variants of th… | Y | N → N |  | 2.0 s | 2 |  |
| Q6 | need → cart (non-food) | I need to deep-clean my bathroom this weekend. | Toilet Cleaner; Toilet Brush - Dual; Disinfectant Bathroom Cleaner - Lemon | Y | Your bathroom deep‑clean kit was assembled from the ‘Bathroom deep‑clean’ kit, a synthetic item list written by the BasketBuddy team (products and prices are real BigBasket data). It includes essentials like Toilet Cleaner ₹149 [P13659] and Disinfectant Floor… | Y | N → N | 0 forbidden | 3.1 s | 1 |  |
| Q7 | need → cart (non-food) | Skincare routine for oily skin under ₹1000. | Oil Control Face Wash - Sulphate Free; Purifying Toner; Oil Control Moisturiser | Y | The suggested skincare set comes from the 'Oily-skin routine' kit, a synthetic item list written by the BasketBuddy team (products and prices are real BigBasket data). It includes a gentle Oil Control Face Wash at ₹80.75 [P6538] and a high‑SPF Ultra Sheer Dry… | Y | N → N |  | 1.3 s | 1 |  |
| Q8 | cross-category | coconut oil for hair | Advansed Aloe Vera Enriched Coconut Hair Oil; Coconut Oil; Advansed Aloe Vera Enriched Coconut Hair Oil | Y | Here are the coconut‑oil‑for‑hair options in the data (the catalogue does not record pack sizes, so variants with different prices are listed separately): - **Parachute Advansed Aloe Vera Enriched Coconut Hair Oil** – ₹78, rating 4.3/5 [P9323] - **Patanjali C… | Y | N → N | 5/5 in expected | 1.4 s | 2 |  |
| Q9 | cross-category | coconut oil for cooking | Organic Coconut Oil/Narial Tel; Organic Coconut Oil/Narial Tel; Coconut Oil - Cold Pressed | Y | Here are the coconut‑oil options for cooking found in the data (the catalogue does not record pack sizes, so variants are listed only by price): - **Earthon Organic Coconut Oil/Narial Tel** – ₹130, not rated [P22405] - **Earthon Organic Coconut Oil/Narial Tel… | Y | N → N | 5/5 in expected | 2.7 s | 2 |  |
| Q10 | cross-category | Cheapest floor cleaner | Floor Cleaner - Pine White; Floor Cleaner - Pine; Floor Cleaner - Lime | Y | - **Floor Cleaner – Pine White** – ₹50, Rating 4/5 [P19563] - **Floor Cleaner – Pine** – ₹55, Rating 3.7/5 [P7398] - **Floor Cleaner – Lime** – ₹70, Rating 3.8/5 [P451] These are the three lowest‑priced floor cleaners in the data. | Y | N → N | 0 forbidden | 3.2 s | 2 |  |
| Q11 | non-food comparison | Compare Dove and Pears bathing soaps on price and rating. | Care & Protect Moisturising Cream Beauty Bathing Bar; Pure & Gentle Bathing Bar; Go Fresh Moisture Bathing Bar Soap | Y | **Dove bathing soaps** - Care & Protect Moisturising Cream Beauty Bathing Bar – Price **₹199**, Rating **4.3/5** [P15030] - Go Fresh Moisture Bathing Bar Soap – Price **₹50**, Rating **4.2/5** [P8528] - Cream Beauty Bathing Bar – Price **₹340**, Rating **4.3/… | Y | N → N |  | 2.0 s | 2 |  |
| Q12 | unsupported | Is paneer in stock in Koramangala right now? |  | n/a | I can't answer that from the BigBasket catalogue data I have. _Why: stock, delivery and availability aren't in the dataset (it's a static price snapshot)._ I can build a cart for a dish or a need (e.g. *paneer butter masala for 4*, *deep-clean my bathroom*), … | n/a | Y → Y (out_of_scope) |  | 0.0 s | 0 |  |
| Q13 | unsupported | Is Blinkit cheaper than BigBasket? |  | n/a | I can't answer that from the BigBasket catalogue data I have. _Why: the dataset only covers BigBasket, so it can't compare other shopping apps._ I can build a cart for a dish or a need (e.g. *paneer butter masala for 4*, *deep-clean my bathroom*), or look up … | n/a | Y → Y (out_of_scope) |  | 0.0 s | 0 |  |
| Q14 | unsupported (off-catalogue) | best laptop under 50000 |  | n/a | I can't answer that from the BigBasket catalogue data I have. _Why: question is about laptops, not grocery/household products._ I can build a cart for a dish or a need (e.g. *paneer butter masala for 4*, *deep-clean my bathroom*), or look up and compare BigBa… | n/a | Y → Y (out_of_scope) |  | 0.8 s | 1 |  |
| Q15 | need → AI list (confirmed) | I'm getting a pet tortoise, what should I buy? |  | N | I can't answer that from the BigBasket catalogue data I have. _Why: none of the items matched a BigBasket product._ I can build a cart for a dish or a need (e.g. *paneer butter masala for 4*, *deep-clean my bathroom*), or look up and compare BigBasket product… | n/a | N → Y (no_match) |  | 1.4 s | 1 |  |
| Q16 | dish (sidebar servings) | paneer butter masala | Organic Tofu - Soy Paneer; Tomato - Local, Organically Grown; Chilli Powder/Menasina Pudi | Y | This cart was generated from the **ingredients of the recipe ‘Paneer Butter Masala’ from the Archana’s Kitchen recipe dataset (serves 4; quantities scaled ×1.5 for 6 people)**. It includes items such as Organic Tofu – Soy Paneer at ₹85.14 [P20], Premium Butte… | Y | N → N |  | 2.0 s | 1 |  |

## 2. Retrieval ablation

46 labelled sub-queries across all domains. A top-5 result is relevant when its sub-category is in the labelled set and (if given) its name contains a labelled word. Wrong-domain = the product's domain differs from the query's labelled domain.

| variant | hit@5 | MRR@5 | wrong-domain top-1 | wrong-domain share of top-5 |
|---|---|---|---|---|
| a. vector only | 0.978 | 0.888 | 0.000 | 0.017 |
| b. hybrid (vector + BM25) | 0.957 | 0.890 | 0.043 | 0.022 |
| c. hybrid + category filter | 0.978 | 0.962 | 0.000 | 0.000 |
| d. hybrid + inferred domain | 0.978 | 0.962 | 0.000 | 0.000 |

- (c) uses the labelled (gold) domain, as the cart path does: recipe ingredients are food and kit items carry their domain. Its wrong-domain rate is 0 by construction; the measured gain is in hit@5.
- (d) is the catalogue path: the domain is *inferred* from the question (purpose phrase, keyword rules, then a retrieval vote) and no filter is used when the vote isn't confident.

<details><summary>Per-query top-1 (b: hybrid, no filter vs c: hybrid + filter)</summary>

| query | b hit@5 | b top1 | c hit@5 | c top1 |
|---|---|---|---|---|
| tomato | 1 | Tomato - Hybrid | 1 | Tomato - Hybrid |
| onion | 1 | Sambhar Onion (Small Onion) | 1 | Onion |
| green chillies | 1 | Chilli - Green, Organically Grown | 1 | Chilli - Green, Organically Grown |
| paneer | 1 | Malai Fresh Paneer | 1 | Malai Fresh Paneer |
| butter | 1 | Premium Butter - Salted | 1 | Premium Butter - Unsalted |
| haldi | 1 | Haldi Powder - Supports Anti-inflammatory Response, As Antioxidant & Healthy Immunity | 1 | Organic Turmeric Powder/Haldi/Arisina Pudi |
| jeera | 1 | Organic Cumin Seeds/Jeera/Jeerige | 1 | Organic Cumin Seeds/Jeera/Jeerige |
| coconut oil for cooking | 1 | Cooking Spray - Coconut Oil | 1 | Organic Coconut Oil/Narial Tel |
| toor dal | 1 | Toor/Arhar Dal 2 kg + Moong Dal 1 kg+Moong - Green, Whole/Sabut 1 kg | 1 | Toor/Arhar Dal 2 kg + Moong Dal 1 kg+Moong - Green, Whole/Sabut 1 kg |
| basmati rice | 1 | Basmati Rice/Basmati Akki - White Sella | 1 | Basmati Rice/Basmati Akki - White Sella |
| whole wheat atta | 1 | Whole Wheat Atta/Godihittu - Pesticide Free | 1 | Whole Wheat Atta/Godihittu - Pesticide Free |
| salt | 1 | Lite - 15% Low Sodium Iodised Salt | 1 | Lite - 15% Low Sodium Iodised Salt |
| ghee | 1 | Ghee/Tuppa | 1 | Ghee/Tuppa |
| rolled oats | 1 | Rolled Oats | 1 | Rolled Oats |
| lemon | 1 | Lemon Essentail Oil - 100% Pure & Natural | 1 | Lemon |
| besan | 1 | Besan/Kadale Hittu | 1 | Besan/Kadale Hittu |
| dhaniya patta | 0 | Powder - Coriander (Dhaniya) | 1 | Coriander Leaves |
| sarson ka tel | 1 | Organic Mustard Oil/Rai Ka Tel | 1 | Organic Mustard Oil/Rai Ka Tel |
| nimbu | 0 | Nimbu Achar | 0 | Nimbu Achar |
| coconut oil for hair | 1 | Advansed Aloe Vera Enriched Coconut Hair Oil | 1 | Advansed Aloe Vera Enriched Coconut Hair Oil |
| face wash for oily skin | 1 | Face Wash - Oily Skin | 1 | Face Wash - Oily Skin |
| face cleanser | 1 | Face Cleanser | 1 | Face Cleanser |
| shampoo | 1 | Dryness Care Shampoo | 1 | Dryness Care Shampoo |
| sunscreen | 1 | Sunblock Sport Sunscreen SPF 50, Non Greasy & Water Resistant | 1 | Sunblock Sport Sunscreen SPF 50, Non Greasy & Water Resistant |
| toothpaste | 1 | Toothpaste - Whitening, Cavity Protection, 150 G | 1 | Toothpaste - Whitening, Cavity Protection, 150 G |
| bathing soap | 1 | Bathing Soap - Aloe Vera | 1 | Bathing Soap - Aloe Vera |
| hand wash | 1 | Lemon Hand Wash | 1 | Lemon Hand Wash |
| deodorant | 1 | Do It Deodorant | 1 | Do It Deodorant |
| oats face scrub | 1 | Charcoal Face Scrub For Exfoliation & Tan Removal - Tea Tree & Peppermint | 1 | Clear Complexion Whitening Face Scrub |
| floor cleaner | 1 | Floor Cleaner - Neem & Pine | 1 | Floor Cleaner - Neem & Pine |
| toilet cleaner | 1 | Toilet Cleaner | 1 | Toilet Cleaner |
| dishwash liquid | 1 | Dishwash Liquid - Lime | 1 | Dishwash Liquid - Lime |
| detergent powder | 1 | Detergent Powder - Lemon & Orange | 1 | Detergent Powder - Lemon & Orange |
| garbage bags | 1 | Garbage Bags - Compostable, 100% Corn Starch, Large, Green, Mono-Carton(24x32 Inches) | 1 | Garbage Bags - Compostable, 100% Corn Starch, Large, Green, Mono-Carton(24x32 Inches) |
| mosquito repellent | 1 | Naturals Mosquito Repellent Spray | 1 | Naturals Mosquito Repellent Spray |
| kitchen wipes | 1 | Kitchen Cloth - Small | 1 | Kitchen Cloth - Small |
| non-stick tawa | 1 | Non-Stick Flat Dosa Tawa - 260 mm, Aluminium, Assorted Colour | 1 | Non-Stick Flat Dosa Tawa - 260 mm, Aluminium, Assorted Colour |
| steel lunch box | 1 | Steel Lunch Box/Tiffin Set With Bag - Klip Lock | 1 | Steel Lunch Box/Tiffin Set With Bag - Klip Lock |
| pressure cooker | 1 | Pressure Cooker - Silver, MM25 | 1 | Pressure Cooker - Silver, MM25 |
| water bottle | 1 | Water Bottle - H2O Steel, Silver | 1 | Water Bottle - H2O Steel, Silver |
| baby diapers | 1 | Baby Dry Medium - 20 Diapers | 1 | Baby Dry Medium - 20 Diapers |
| baby wipes | 1 | Baby Wipes - Combo Pack | 1 | Baby Wipes - Combo Pack |
| baby shampoo | 1 | Rich Moisture Baby Shampoo | 1 | Rich Moisture Baby Shampoo |
| dog food | 1 | Premium Adult Wet Dog Food - White Meat Fish & Vegetables | 1 | Premium Adult Wet Dog Food - White Meat Fish & Vegetables |
| cat litter | 1 | Hygiene Plus Non Clumping 100% Natural Cat Litter | 1 | Hygiene Plus Non Clumping 100% Natural Cat Litter |
| potting soil for plants | 1 | Neem Mix | 1 | Neem Mix |

</details>

## 3. Re-checking SIM_THRESHOLD

Catalogue-style questions with an answer in the catalogue (`on`, 15) and without (`off`, 15), run through the real catalogue path (decompose → retrieve_catalogue). Best vector similarity per question; `stopped_by` is the first check that stopped it.

- `on` best similarity: min **0.710**, mean 0.793
- `off` best similarity: max **0.692**, mean 0.650 (8 stopped before retrieval)

| threshold | answerable kept | unanswerable blocked |
|---|---|---|
| 0.64 | 15/15 | 2/7 |
| 0.66 | 15/15 | 4/7 |
| 0.68 | 15/15 | 5/7 |
| 0.70 | 15/15 | 7/7 |
| 0.72 | 14/15 | 7/7 |
| 0.74 | 14/15 | 7/7 |

<details><summary>Per-question similarities</summary>

| group | query | stopped_by | best_sim |
|---|---|---|---|
| on | Fortune sunflower oil price | answered | 0.805 |
| on | cheapest floor cleaner | answered | 0.784 |
| on | Dove soap price | answered | 0.820 |
| on | compare toothpaste brands | answered | 0.748 |
| on | best rated shampoo | answered | 0.710 |
| on | basmati rice under 200 | answered | 0.814 |
| on | dog food price | answered | 0.798 |
| on | baby diapers price | answered | 0.796 |
| on | green tea brands | answered | 0.805 |
| on | non-stick tawa price | answered | 0.842 |
| on | mosquito repellent | answered | 0.800 |
| on | instant noodles | answered | 0.797 |
| on | dark chocolate | answered | 0.774 |
| on | garbage bags price | answered | 0.796 |
| on | sunscreen for oily skin | answered | 0.803 |
| off | best laptop under 50000 | scope rule |  |
| off | iPhone 15 price | scope rule |  |
| off | cricket bat | scope rule |  |
| off | sofa set for living room | scope rule |  |
| off | washing machine 7 kg | threshold | 0.691 |
| off | car tyres | scope rule |  |
| off | running shoes for men | scope rule |  |
| off | office chair | threshold | 0.645 |
| off | LED television 43 inch | threshold | 0.672 |
| off | gold necklace | scope rule |  |
| off | bicycle for kids | scope rule |  |
| off | airpods case | threshold | 0.613 |
| off | printer ink cartridge | threshold | 0.692 |
| off | air conditioner 1.5 ton | threshold | 0.599 |
| off | wooden study table | threshold | 0.641 |

</details>
