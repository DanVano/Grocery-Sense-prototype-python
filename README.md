# Grocery Sense — Python prototype (archived)

The original Python/Tkinter prototype of [Grocery Sense](https://github.com/DanVano/Grocery_Sense).
I built this first to work out the hard parts — receipt ingestion, price history, fuzzy product
matching across stores, and the shopping/trip planning logic — before committing to the real app.

Once the logic held up against real receipts, I rewrote the whole thing in C#/.NET 10 with MAUI
Blazor so it could run on Android and iOS. The Python behaviour was carried over as golden-file
test fixtures in the C# suite, so the port is verified against this version rather than just
reimplemented.

This repo is kept as a reference and is no longer developed. Go to
**[Grocery_Sense](https://github.com/DanVano/Grocery_Sense)** for the current app.
