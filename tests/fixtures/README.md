# Sample data (fictional)

"Northpace" is a made-up brand. These numbers are sample data for the Counter demo, not real inventory.

- `inventory_sample.csv`: Counter's CSV format (`sku,name,units_on_hand,units_sold_28d,price,unit_cost,description,url`).
- `shopify_products_import.csv`: the same products in Shopify's product import format
  (Shopify admin > Products > Import). Includes inventory quantity and cost per item.
  Shopify's importer can change column requirements; if it rejects a column, export one product
  from your store and match its header.
  After import, create a few test orders so the 28-day velocity is not zero.
