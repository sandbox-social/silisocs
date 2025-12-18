# Social Sandbox Dashboard - Restructured

## Project Structure

```
dashboard/
├── config.py                 # Configuration and constants
├── data_processing.py        # Data loading and processing utilities
├── graph_utils.py           # Graph layout and visualization utilities
├── ui_components.py         # UI component builders
├── styles.py                # Cytoscape stylesheet generation
├── layout.py                # Dashboard layout components
├── main.py                  # Main application entry point
└── README.md               # This file
```

## Module Descriptions

### config.py
Contains all configuration constants including:
- Project paths
- Probe labels
- Custom agent names
- Color schemes for visualization
- Interaction type mappings

### data_processing.py
Handles all data loading and processing:
- `post_process_output()` - Extract different event types from dataframes
- `get_toot_dict()` - Process toot/post data
- `get_int_dict()` - Process interaction data
- `load_data_from_folder()` - Main data loading function
- `serialize_data()` / `deserialize_data()` - Data persistence helpers
- `stream_filtered_jsonl()` - Stream processing for large files

### graph_utils.py
Graph visualization and plotting utilities:
- `compute_positions()` - Calculate node positions using Kamada-Kawai layout
- `probe_plot_preprocessing()` - Prepare probe data for visualization
- `create_probe_data_figure()` - Generate vote distribution plots
- `create_interactions_figure()` - Generate interaction timeline plots

### ui_components.py
Reusable UI component builders:
- `create_display()` - Display for prompt/output entries
- `create_display_plan()` - Display for plan and action entries
- `create_interaction_display()` - Display for individual interactions
- `convert_linebreaks()` - HTML formatting helper

### styles.py
Cytoscape stylesheet management:
- `get_base_stylesheet()` - Base styles for nodes and edges
- `build_stylesheet()` - Dynamic stylesheet based on current state
- `build_cytoscape_elements()` - Build node and edge elements

### layout.py
Dashboard layout components:
- `get_index_string()` - HTML template with custom CSS/JS
- `create_app_layout()` - Complete app layout structure
- `create_upload_section()` - File upload interface
- `create_dashboard_section()` - Main dashboard view
- Various helper functions for specific UI sections

### main.py
Main application orchestration:
- Command-line argument parsing
- Data initialization
- Dash app creation
- Callback registration
- Server startup

## Usage

### Running the Dashboard

```bash
# With a directory containing JSONL files
python main.py --output_dir /path/to/output/folder

# Legacy mode with single file (if implemented)
python main.py --output_file /path/to/output.jsonl
```

### Required Files in Output Directory
- `action_events.jsonl` (required)
- `probe_events.jsonl` (required)
- `prompts_and_responses.jsonl` (optional, for agent thoughts)

## Modifying for New Data Structure

When the input data structure changes, you'll primarily need to update:

1. **data_processing.py** - Adjust the parsing functions:
   - `post_process_output()` - Update field extraction
   - `get_toot_dict()` / `get_int_dict()` - Modify data transformation
   - `load_data_from_folder()` - Change file loading logic

2. **config.py** - Update constants if needed:
   - Probe labels
   - Interaction types
   - Field mappings

3. **graph_utils.py** - If visualization logic changes:
   - Update aggregation functions
   - Modify plot generation

The modular structure makes it easier to:
- Locate relevant code quickly
- Test individual components
- Make isolated changes without affecting other parts
- Reuse components across different dashboards

## Benefits of This Structure

1. **Separation of Concerns**: Each module has a clear, single responsibility
2. **Maintainability**: Easier to find and modify specific functionality
3. **Testability**: Each module can be tested independently
4. **Reusability**: Components can be reused in other dashboards
5. **Readability**: Much shorter files, clearer organization
6. **Scalability**: Easy to add new visualizations or data sources

## Next Steps for Data Structure Changes

1. Identify which files changed in your new data structure
2. Update `data_processing.py` to handle the new format
3. Modify any visualization functions in `graph_utils.py` if needed
4. Update constants in `config.py` if field names changed
5. Test incrementally by module

The callbacks in `main.py` remain largely unchanged unless you're adding new interactive features.