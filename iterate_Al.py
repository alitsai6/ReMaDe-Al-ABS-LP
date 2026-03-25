import numpy as np
import pandas as pd
import pathlib
import gurobipy as gp
from gurobipy import GRB
from domestic_Al import optimize, result_calculations, generate_snapshot

objective = "primary"

scenarios_references = pd.read_excel('domesticABS_scenarios.xlsx', index_col=[0], sheet_name="Table")

# SPECIFY HERE which scenarios to analyze

scenarios = ["Baseline", "Low GR", "High GR", "Greene",
			 "Separate Manufacturing Scrap", "Shifting 5x to 6x Demand", "P0610", "P0000", "HRC Compositions", "No FCC",
			 "93% Accurate LIBS", "100% Accurate LIBS", "LIBS fixed comps", "LIBS Kelly", "Castings Removal", "Isolated Ford Shredding", 
			 "LIBS + HRC", "LIBS + 5x->6x", "LIBS + Sep Manuf Scrap", "HRC + 5x->6x", "LIBS + Sep Manuf + HRC", "LIBS + HRC + 5x->6x", "All",
			 "LIBS 100% + HRC", "LIBS 100% + 5x->6x", "LIBS 100% + Sep Manuf Scrap", "LIBS 100% + Sep Manuf + HRC", "LIBS 100% + HRC + 5x->6x", "All with 100% accurate LIBS",
			 "LIBS fixed comps + HRC", "LIBS fixed comps + 5x->6x", "LIBS fixed comps + Sep Manuf Scrap", "LIBS fixed comps + Sep Manuf + HRC", "LIBS fixed comps + HRC + 5x->6x", "All with LIBS fixed comps",
			 "LIBS Kelly + HRC", "LIBS Kelly + 5x->6x", "LIBS Kelly + Sep Manuf Scrap", "LIBS Kelly + Sep Manuf + HRC", "LIBS Kelly + HRC + 5x->6x", "All with LIBS Kelly"
			 ]

# Define the mapping of result_calculations keys to export labels
result_key_mapping = {
    "prim_preML": 'Primary',
    "secvirg_preML": 'Secondary_Virgin', 
    "forming_scrapsum_RR": 'Forming_scrap',
    "fabrication_scrapsum_RR": 'Fabrication_scrap',
    "eol_scrapsum_RR": 'EOL_scrap',
    "RR_forming": 'RR_forming',
    "RC_forming": 'RC_forming', 
    "RR_fabrication": 'RR_fabrication',
    "RC_fabrication": 'RC_fabrication',
    "RR_manuf": 'RR_manuf',
    "RC_manuf": 'RC_manuf',
    "RR_EOL": 'RR_EOL',
    "RC_EOL": 'RC_EOL',
    "RR": 'RR',
    "RC": 'RC',
	'emissions_frozen': 'Emissions_frozen',
	'emissions_moderate': 'Emissions_moderate',
	'emissions_aggressive': 'Emissions_aggressive'
}

export = {'Value': tuple(result_key_mapping.values())}

for scenario in scenarios:
	print("Now running scenario: " + scenario)
	scenario_row = scenarios_references.loc[[scenario]]
	label = scenario_row["label"].values[0]


	# Set folder path based on objective
	if objective == "emissions":
		folder = r'RESULTS/' + scenario + '/'
	elif objective == "primary":
		folder = r'RESULTS/' + scenario + '/'
	pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
	
	# Initialize dataframe to accumulate EOL scrap compositions across all years
	eol_comps_accumulated = pd.DataFrame()

	for year in range(2020, 2051):

		model = optimize(objective, year, scenario)
		results_dict = result_calculations(model, year, scenario)
		# Extract only the specified values in the correct order
		export[year] = [results_dict[key] for key in result_key_mapping.keys()]
		
		# Extract EOL scrap compositions and add year to index
		eol_comps = results_dict['eol_scrap_comps'].copy()
		eol_comps.index = eol_comps.index + f' [{year}]'
		eol_comps_accumulated = pd.concat([eol_comps_accumulated, eol_comps])

		# Only generate snapshots for select years to save time and storage, but can adjust as needed
		if year == 2035 or year == 2025 or year == 2050:
			generate_snapshot(model, year, scenario, folder)
		else:
			continue
		
		print("Data successfully written for year: "+ str(year))

	print("All years complete")

	# Convert data into dataframe
	print("Converting data to dataframe")
	df = pd.DataFrame(export)

	# save to xlsx file
	filepath = folder + 'domesticABS_' + label + '_raw.xlsx'

	df.to_excel(filepath, index=False)
	
	# Save EOL scrap compositions to separate Excel file
	eol_comps_filepath = folder + 'domesticABS_' + label + '_eol_comps.xlsx'
	eol_comps_accumulated.to_excel(eol_comps_filepath)