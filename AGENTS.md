## Current State
- Everything in this repository is currently in an early draft state.
- Nothing is formalized, and all code, structure, and architecture is subject to change.
- The project is highly experimental at this phase.

## Folder Layout

- `docs/`: Project documentation.
- `raw_data/`: Raw source data (not processed) and reference materials. Transformed into the data stored in `src/data/`.
- `src/config/`: Configuration files (e.g., sample generation settings).
- `src/data/`: Processed data and model inputs/outputs.
- `src/data_builders/`: Scripts to build processed data from raw data.
- `src/ml/`: Machine learning models and feature engineering.
- `src/opt/`: Optimizer and operations research models.

## Python Style Guide
- Use Polars over Pandas.
- Use PyTorch over TensorFlow.
- Use `uv` instead of `pip` as package manager, and runner.
- Use strict typing and type hints.

## Future
- Create two folders: backend, and frontend. backend will be written in Python+FastAPI, and frontend will be written in React+TypeScript.