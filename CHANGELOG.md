# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.4.1] - 2026-04-06
### Fixed
- Fixed favicon download tracking bug in `_download_favicon_async` method
- Improved `finally` block handling to properly clean up download state for each favicon service
- Resolved issue where favicons would intermittently fail to display due to improper mutex tracking

## [2.4.0] - 2024-04-05
### Added
- Increased search results limit from 10 to 100
- Multiple favicon services with fallback strategy (DuckDuckGo, Google)
- Improved favicon caching with multi-service cache checking

### Changed
- Reduced favicon download timeout from 2s to 1s
- Increased parallel download threads to 20 with staggered start

## [2.3.0] - 2024-04-05
### Added
- Full debounce optimization implementation
- Cleaned up query listener module
- Improved request handling and caching

## [2.2.0] - 2024-04-05
### Added
- Debounce optimization strategy
- Increased input_debounce from 0.2s to 0.4s
- In-flight request detection for duplicate queries
- Partial cache matching with prefix-based results

## [2.1.0] - 2024-04-05
### Added
- Asynchronous favicon download support

## [2.0.0] - 2024-04-05
### Added
- Tag selection feature implementation
- Custom keyword support trial and adjustment

### Changed
- API v2 → v3 migration
- Removed `tag_keyword` preference from manifest.json
- Removed `tag_keyword` reference from `on_input` method
- Added and removed `_tag_search_keyword_prefix` variable
- Implemented `on_item_enter` method for direct search result return

### Removed
- Query update feature (experimental)

## [1.5.0] - 2024-04-05
### Added
- Enhanced caching system with dynamic TTL adjustment based on cache hit/miss ratio.
- Improved error handling for network requests and general exceptions.
- Added cache statistics and TTL information to the rdopen menu.

### Changed
- Renamed `参考` folder to `reference` for better clarity.
- Updated `.gitignore` to exclude the `reference` folder.

## [1.4.2] - 2024-04-05
### Added
- Added `favicon_cache/` and `search_cache/` to `.gitignore` to exclude cache directories from version control.

## [1.4.1] - 2024-04-04
### Changed
- Optimized API timeout from 5s to 3s for better responsiveness.

## [1.4.0] - 2024-04-04
### Added
- Enhanced caching system for improved performance.
- Display extension version in rdopen menu.

## [1.3.5] - 2024-03-28
### Added
- Initial release of the enhanced caching system.
