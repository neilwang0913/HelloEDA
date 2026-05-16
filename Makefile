PYTHON  = python3
PLANNER = peak_window_planner.py
SCRAPER = mxp_flight_scraper.py

-include .env
export

.PHONY: all plan scrape opensky live demo clean

all: plan

plan:
	$(PYTHON) $(PLANNER)

live:
	$(PYTHON) $(SCRAPER) --source opensky --html --md

opensky:
	$(PYTHON) $(SCRAPER) --source opensky --html --md

scrape:
	$(PYTHON) $(SCRAPER) --source github --html --md

demo:
	$(PYTHON) $(SCRAPER) --demo --html --md

clean:
	rm -rf __pycache__ arrivals_mxp.csv
