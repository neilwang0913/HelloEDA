PYTHON  = python3
PLANNER = peak_window_planner.py
SCRAPER = mxp_flight_scraper.py

# 如果存在 .env 文件，自动加载环境变量（OPENSKY_USER / OPENSKY_PASS）
-include .env
export

.PHONY: all plan scrape opensky live demo clean

all: plan

plan:
	$(PYTHON) $(PLANNER)

# 实时接口：当前正在进近 LIMC 的航班（无需研究员权限，立即可用）
live:
	$(PYTHON) $(SCRAPER) --source opensky --html --md

# 历史接口（需 OpenSky 研究员账号，普通账号自动降级到 live）
opensky:
	$(PYTHON) $(SCRAPER) --source opensky --html --md

# 使用 GitHub 仓库中的 CSV 文件
scrape:
	$(PYTHON) $(SCRAPER) --source github --html --md

# 使用内置 Demo 数据（无需账号或网络）
demo:
	$(PYTHON) $(SCRAPER) --demo --html --md

clean:
	rm -rf __pycache__ arrivals_mxp.csv
