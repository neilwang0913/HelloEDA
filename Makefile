PYTHON = python3
PLANNER = peak_window_planner.py
SCRAPER  = mxp_flight_scraper.py

.PHONY: all plan scrape clean

all: plan

plan:
	$(PYTHON) $(PLANNER)

scrape:
	$(PYTHON) $(SCRAPER)

clean:
	rm -rf __pycache__
