"""
Test score_meh_vibe() function with example titles/snippets
"""
from scraper import score_meh_vibe

print("=" * 60)
print("VIBE SCORING TEST")
print("=" * 60)

# Test 1: Strong Meh-style site
test1_title = "A Daily Deal & Community for the Cynical Consumer - Meh"
test1_snippet = "Meh is your daily deal site with witty, sarcastic commentary on one product per day."
score1 = score_meh_vibe(test1_title, test1_snippet)
print(f"\nTest 1: Strong Meh-style site")
print(f"Title: {test1_title}")
print(f"Snippet: {test1_snippet}")
print(f"Score: {score1}/10")
print(f"Expected: High (7-10)")

# Test 2: Aggregator (should score low)
test2_title = "Groupon: Deals and Coupons for 50-90% off"
test2_snippet = "Save up to 90% with thousands of daily deals from local businesses."
score2 = score_meh_vibe(test2_title, test2_snippet)
print(f"\nTest 2: Aggregator (Groupon)")
print(f"Title: {test2_title}")
print(f"Snippet: {test2_snippet}")
print(f"Score: {score2}/10")
print(f"Expected: Low (0-3)")

# Test 3: Neutral deal site
test3_title = "Today's Daily Deal: Wireless Earbuds"
test3_snippet = "Get great prices on electronics with our daily flash sale."
score3 = score_meh_vibe(test3_title, test3_snippet)
print(f"\nTest 3: Neutral deal site")
print(f"Title: {test3_title}")
print(f"Snippet: {test3_snippet}")
print(f"Score: {score3}/10")
print(f"Expected: Medium (4-6)")

# Test 4: Generic Shopify store (should have penalty)
test4_title = "Amazing Daily Deals"
test4_snippet = "Shop at mystore.myshopify.com for the best prices."
score4 = score_meh_vibe(test4_title, test4_snippet)
print(f"\nTest 4: Generic Shopify store")
print(f"Title: {test4_title}")
print(f"Snippet: {test4_snippet}")
print(f"Score: {score4}/10")
print(f"Expected: Low (0-3) due to Shopify penalty")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Strong Meh-style: {score1}/10 ✓" if score1 >= 7 else f"Strong Meh-style: {score1}/10 ✗")
print(f"Aggregator: {score2}/10 ✓" if score2 <= 3 else f"Aggregator: {score2}/10 ✗")
print(f"Neutral deal: {score3}/10 ✓" if 4 <= score3 <= 6 else f"Neutral deal: {score3}/10 ✗")
print(f"Shopify penalty: {score4}/10 ✓" if score4 <= 3 else f"Shopify penalty: {score4}/10 ✗")
print("=" * 60)
