# H3 Analysis

A Streamlit application for exploring time-bucketed metrics on an H3 grid. The
current dataset contains UAE audience segments, two-hour buckets, and user counts.

## Features

- Loads the local default CSV or a CSV uploaded in the browser
- Filters multiple audience segments with checkboxes
- Switches between two-hour buckets
- Aggregates user counts by H3 cell
- Renders interactive H3 polygons with PyDeck
- Offers dark and alternate basemap views
- Displays the filtered aggregate as a table
- Shows a second map for the index dataset, switchable between
`exclusivity_index` and `volume_index`
- Shows a third map for `overall_index`, switchable between Filtered
(`analysis_indexed_filtered`) and Full (`analysis_indexed`)

