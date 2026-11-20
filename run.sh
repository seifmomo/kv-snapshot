#!/usr/bin/env sh
# Start the dashboard with sample data and open the browser.
exec python3 -m kvstore --demo --open "$@"
