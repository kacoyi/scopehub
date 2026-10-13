# ScopeHub

ScopeHub is a planned lightweight market workbench for human–AI collaboration, market data visualization, and indicator exploration.

## Current status

The project is at the engineering specification stage. No runnable application or installer is available yet.
The [v1 engineering contract](engineering-contract-v1.json) defines the product scope, architecture, protocols, milestones, and acceptance criteria.

## Planned v1 scope

- A desktop application for Windows 10/11 x64.
- Simulated market data and local historical samples, with a single workspace and instrument chart, candlestick and line styles, and timeframe selection.
- AI conversations, tool operations, indicator previews, operation history, and workspace restoration.
- An offline demo mode. The WebView2 bootstrapper may require an internet connection during installation.

The planned stack uses React, TypeScript, and Tauri 2 for the interface and Rust application core, SQLite for persistence, and controlled Python workers for market data, AI, and indicator tasks.

V1 excludes live trading, orders, and account integration. Real market data integration requires subsequent verification of data sources, permissions, and redistribution licenses.

## Contributing

Use issues to discuss requirements or suggest improvements, and pull requests to improve the engineering specification. Implementation must follow the scope, dependency rules, and acceptance criteria in the engineering contract.

## Repository language

English is the default language for repository content, including documentation, specifications, source comments, commit messages, issues, and pull requests.

## License

This project is licensed under the [MIT License](LICENSE).
