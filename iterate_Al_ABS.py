import numpy as np
import pandas as pd
import subprocess, sys
import pathlib
import gurobipy as gp
from gurobipy import GRB
# subprocess.Popen(r'test.py', shell=True)
from domestic_Al_ABS import optimize_ABS, optimize_ABS_LIBS

objective = "primary"

scenarios_references = pd.read_excel('domesticABS_scenarios.xlsx', index_col=[0], sheet_name="Table")

# SPECIFY HERE which scenarios to analyze
scenarios = ["Baseline", "Low GR", "High GR", "Greene", "Separate Manufacturing Scrap", "Shifting 5x to 6x Demand", "P0610", "P0000", "HRC Compositions", "No FCC", "LIBS",
			 "Castings Removal", "Isolated Ford shredding", "LIBS + HRC", "LIBS + 5x->6x", "LIBS + Sep Manuf Scrap", "HRC + 5x->6x", "LIBS + HRC + 5x->6x",
			 "All"]

# scenarios = ["All"]

# scenarios = ["Baseline", "Low GR", "High GR", "Greene", "Separate Manufacturing Scrap", "Shifting 5x to 6x Demand", "P0610", "HRC Compositions",
# 			 "No FCC", "LIBS", "Castings Removal", "Isolated Ford shredding"]

# scenarios = ["LIBS", "LIBS + HRC", "LIBS + HRC", "LIBS + 5x->6x", "LIBS + Sep Manuf Scrap", "HRC + 5x->6x", "LIBS + HRC + 5x->6x", "All"]

# scenarios = ["LIBS + Sep Manuf Scrap"]

# scenarios = ["Shifting 5x to 6x Demand", "P0610", "HRC Compositions",
# 			 "No FCC", "LIBS", "Castings Removal", "Isolated Ford shredding"]

# scenarios = ["LIBS", "Castings Removal", "Isolated Ford shredding"]

# scenarios = ["LIBS"]

# scenarios = ["HRC Compositions"]

# scenarios = ["P0404", "P0303"]
# scenarios = ["P0000"]

export = {'Value':('Primary', 'Secondary_Virgin', 'EOL_scrap', 'Manufacturing scrap','RR', 'RC', 'RR_EOL', 'RC_EOL', 'RR_manuf', 'RC_manuf', 'Emissions (Fro)', 'Emissions (Mod)', 'Emissions (Aggr)')}

for scenario in scenarios:
	print("Now running scenario: " + scenario)
	scenario_row = scenarios_references.loc[[scenario]]
	label = scenario_row["label"].values[0]

	if objective == "emissions":
		folder = r'/Users/alissatsai/Documents/Winter 2025 University of Michigan/Aluminum optimization/Domestic ABS Results EOL cap (min emissions)/' + scenario + '/'
	elif objective == "primary":
		folder = r'/Users/alissatsai/Documents/Winter 2025 University of Michigan/Aluminum optimization/Domestic ABS Results EOL cap (min prim)/' + scenario + '/'

	pathlib.Path(folder).mkdir(parents=True, exist_ok=True)

	for year in range(2020, 2051):

		if year == 2035 or year == 2025 or year == 2050:
			snapshot = True
		else:
			snapshot = False

		if ("LIBS" in scenario or "All" in scenario) and year > 2025:
			export[year] = optimize_ABS_LIBS(objective, year, scenario, snapshot, folder)
			print("Data successfully written for year: "+ str(year))
		else:
			export[year] = optimize_ABS(objective, year, scenario, snapshot, folder)
			print("Data successfully written for year: "+ str(year))

	print("All years complete")

	# # print(sys.version)

	# # print(x)
	# # print(type(x))

	# Convert data into dataframe
	print("Converting data to dataframe")
	df = pd.DataFrame(export)

	# save to xlsx file

	
	filepath = folder + 'domesticABS_' + label + '_raw.xlsx'

	df.to_excel(filepath, index=False)