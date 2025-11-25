# Alissa Tsai <alitsai@umich.edu.
# 1/2025 
# Optimize quantity of domestic aluminum scrap used in aluminum alloy sheet products

import numpy as np
import gurobipy as gp
from gurobipy import GRB
import pandas as pd
import json
import sys

def optimize_ABS(objective, year, scenario, snapshot, folder):
    try:
        # Create a new model
        m = gp.Model("Al-opt")

        # Read and process data
        scenarios_references = pd.read_excel('domesticABS_scenarios.xlsx', index_col=[0], sheet_name="Table")
        scenario_row = scenarios_references.loc[[scenario]]
        baseline_row = scenarios_references.loc[["Baseline"]]
        label = scenario_row["label"].values[0]

        if year < 2026:
            scenario_row = baseline_row

        separation_data_file = scenario_row["separation_data_file"].values[0]
        demand_dict_file = scenario_row["demand_dict_file"].values[0]
        compositions_dict_file = scenario_row["compositions_dict_file"].values[0]
        supply_data_file = scenario_row["supply_data_file"].values[0]
        emissions_data_file_frozen = scenario_row["emissions_data_file_frozen"].values[0]
        emissions_data_file_moderate = scenario_row["emissions_data_file_moderate"].values[0]
        emissions_data_file_aggressive = scenario_row["emissions_data_file_aggressive"].values[0]
        product_parameters_dict_file = scenario_row["product_parameters_dict_file"].values[0]

        separation_data = pd.read_excel(separation_data_file)                                               # *FUTURE IMPROVEMENT* Can integrate Scheil calculation into this program
        demand_dict = pd.read_excel(demand_dict_file, sheet_name=None)
        compositions_dict = pd.read_excel(compositions_dict_file, sheet_name=None)                          # Use this for all shape scenarios
        supply_data = pd.read_excel(supply_data_file)                                                       # Use this for only sheet scenarios
        # supply_dict = pd.read_excel('100CR_sep_domestic_Al_supply.xlsx', sheet_name=None)                 # Use this for all shape scenarios
        emissions_data_frozen = pd.read_excel(emissions_data_file_frozen)
        emissions_data_moderate = pd.read_excel(emissions_data_file_moderate)
        emissions_data_aggressive = pd.read_excel(emissions_data_file_aggressive)
        product_parameters_dict = pd.read_excel(product_parameters_dict_file, sheet_name=None)

        demand_data = pd.concat(demand_dict.values(), ignore_index=True)                                    
        compositions_data = pd.concat(compositions_dict.values(), ignore_index=True)                      # Use this for all shape scenarios
        # supply_data = pd.concat(supply_dict.values(), ignore_index=True)                                  # Use this for all shape scenarios
        product_parameters = pd.concat(product_parameters_dict.values(), ignore_index=True)

        compositions_data.set_index("Alloy", inplace=True, drop=True)
        # emissions_data.set_index("Process", inplace=True, drop=True)
        product_parameters.set_index("Alloy", inplace=True, drop=True)
        demand_list = demand_data['Alloy'].to_numpy()

        emissions_data_frozen.set_index("Process", inplace=True, drop=True)
        emissions_data_moderate.set_index("Process", inplace=True, drop=True)
        emissions_data_aggressive.set_index("Process", inplace=True, drop=True)

        compositions_data = compositions_data.drop(columns=['Al'])              # Removes Al column from compositions data

        #########################################################################################################################################################################################
        """Initialize and set variables to be used later in optimization"""
        #########################################################################################################################################################################################
        num_temps = len(separation_data.index)

        # Set number of products
        num_products = demand_data.shape[0]                            # CHANGE THIS depending on number of products

        # Set number of elements
        # num_elements = 6                            # CHANGE THIS depending on number of elements (including Al)
        # # Al, Cu, Mg, Mn, Si, Fe
        num_elements = len(compositions_data.columns)                              # Number of elements depends on the number of columns in compositions dataframe
        # Al, Cu, Mg, Mn, Si, Fe, Zn

        elements_list = compositions_data.columns.values

        furnace_yield = 0.95

        collection_rate = 0.95

        scrap_proc_yield = scenario_row["scrap_proc_yield"].values[0]                        # Default value of 0.95 (from aluminum production research)
        # scrap_proc_yield = 0.461929619

        # Check supply_data Dataframe if "Fraction" row is listed
        fraction_row = supply_data.loc[supply_data.iloc[:, 0].str.startswith('Fraction')]
        if not fraction_row.empty:
            sheet_from_all_conversion = supply_data.loc[supply_data.iloc[:, 0].str.startswith('Fraction')][year].to_numpy()[0]      # Dataframe of fraction of ABS of Twitch from supply data sheet (use when supply is all Twitch)
        else:
            sheet_from_all_conversion = 1.0
        # sheet_from_all_conversion = 0.473372676098014

        # eol_scrapsources = supply_data.loc[supply_data['Sector'].str.startswith('All')]         # For all scrap streams combined
        # eol_scrapsources = supply_data.loc[~supply_data['Sector'].str.startswith('All')]        # For all separate streams
        eol_scrapsources = supply_data.loc[~supply_data.iloc[:, 0].str.startswith('Fraction')]

        scrap_list = eol_scrapsources.iloc[:, 0].to_numpy()

        # Set number of scrap sources BEFORE separation
        num_mixed = scenario_row["num_mixed"].values[0].item()

        num_scrap = eol_scrapsources.shape[0] + num_products + num_mixed
        # num_scrap = supply_data.shape[0] + num_products + 1
            # EOL scrap stream + manufacturing scrap for each product + mixed 5xxx 6xxx
        # num_scrap = eol_scrapsources.shape[0] + num_products
            # EOL scrap stream + manufacturing scrap for each product d
        

        # Set index of scrap source being separated
        # scrapsep_index = np.array([0])              # CHANGE THIS depending on which source(s) are being separated
        # scrapsep_index = np.array([])

        if pd.isna(scenario_row["scrapsep_index"].values[0]):
            scrapsep_index = np.array([])
        else:
            scrapsep_index = np.array([scenario_row["scrapsep_index"].values[0].astype(int).item()])

        scrap_indices = np.arange(num_scrap)
        nonsep_index = scrap_indices[~np.isin(scrap_indices, scrapsep_index)]

        if scrapsep_index.size > 0:
            num_sep = 2*num_temps
        else:
            num_sep = 0

        # Calculate total number of potential scrap sources
        scrap_source = num_scrap + num_sep                              # This can also be manually changed; total # of scrap sources (including original)

        # Values
        process_yield = []
        alpha_furnace = []


        for i in demand_list:
            process_yield.append(product_parameters.query('Alloy== @i')["Process Yield"])
            alpha_furnace.append(product_parameters.query('Alloy== @i')["Furnace Charge Constraint"])
        # Obtains process yields and furnace charge constraint values for each specified product

        process_yield = pd.concat(process_yield).to_numpy()
        alpha_furnace = pd.concat(alpha_furnace).to_numpy()

        RR_CL = 0.91                                                    # Closed loop manufacturing recycling rate = 91% for sheet: means that 9% of manufacturing scrap is recycled into other products
        # RR_CL = 1                                                     # Use when manufacturing scrap is fully used
        demand = (demand_data[year].to_numpy())/process_yield           

        # print(demand)
        # breakpoint()

        if not scenario_row["scrap_proc_yield in DMFA"].values[0]:
            eol_supply = eol_scrapsources[year].to_numpy() * scrap_proc_yield             # Use when scrap processing yield not built into DMFA supply inputs
        else:
            eol_supply = eol_scrapsources[year].to_numpy()                                  # Use when scrap processing yield built into DMFA supply
        
        # eol_supply = eol_scrapsources[year].to_numpy() * scrap_proc_yield             # Use when scrap processing yield not built into DMFA supply inputs
        # eol_supply = eol_scrapsources[year].to_numpy()                                  # Use when scrap processing yield built into DMFA supply
        manuf_supply = (demand * (1-process_yield)) * RR_CL

 
        mixing = 1                                      # CHANGE THIS to specify how much of the non Ford 5xxx and 6xxx series scrap is mixed

        if pd.isna(scenario_row["to_mix"].values[0]):
            to_mix = np.array([])
        else:
            to_mix = np.array(json.loads(scenario_row["to_mix"].values[0]))

        # to_mix = np.array(["Sheet 5xxx", "Sheet 6xxx 6005C", "Sheet 6xxx 6016"])
        # to_mix = np.array(["Sheet HRC 5xxx Alloy 1", "Sheet HRC 5xxx Alloy 2", "Sheet HRC 6xxx Alloy 1", "Sheet HRC 6xxx Alloy 2"])
        # to_mix = np.array([])

        to_mix_list = []
        quant_before_mix = []
        comps_to_mix = []

        manuf_supply_edited = manuf_supply.copy()

        mixed_manuf_supply = np.array([])


        # Below lines: when the to_mix array contains a list of manufacturing scrap streams to be mixed,
        # iterate through the list of scrap streams to mix together and perform a series of operations
        # that mixes the quantities of these manufacturing scrap streams together
        if to_mix.size != 0:
            for i in to_mix:
                index = demand_data.query('Alloy== @i').index[0]
                to_mix_list.append(manuf_supply[index])
                manuf_supply_edited[index] = (1 - mixing) * manuf_supply[index]
                quant_before_mix.append(manuf_supply[index])
                comps_to_mix.append(compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(i)])


            mixed_manuf_supply = np.array([mixing * (sum(to_mix_list))])

        supply = np.concatenate([eol_supply, manuf_supply_edited, mixed_manuf_supply])

        # Composition definitions

        # Below lines: when the to_mix array contains a list of manufacturing scrap streams to be mixed,
        # iterate through the list of scrap streams to mix together and perform a series of operations
        # that calculates the weight average composition of these manufacturing scrap streams, then adds
        # these new mixed manufacturing scrap stream compositions to the full list of compositions
        if to_mix.size != 0:
            comps_to_mix = pd.concat(comps_to_mix).to_numpy()
            quant_before_mix = np.array(quant_before_mix)

            if mixed_manuf_supply == 0:
                new_row = np.zeros(num_elements)
                # new_row = [0, 0, 0, 0, 0, 0]
            else:
                mixed_comp = ((comps_to_mix * quant_before_mix[:, None]).sum(axis=0)) / mixed_manuf_supply
                new_row = mixed_comp

            compositions_data.loc[len(compositions_data)] = new_row
            compositions_data.rename(index={(len(compositions_data) - 1): 'Mixed ' + str(to_mix) + ' midrange'}, inplace=True)

        eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains(scenario_row["eolscrap_comp keyword"].values[0]) & compositions_data.index.str.contains(str(year))]

        # eolscrap_comp = compositions_data.loc['Sheet Supply']
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Sean Kelly')]
            # Midrange of EOL scrap composition as defined in Kelly thesis
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150') & compositions_data.index.str.contains('UM')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150') & compositions_data.index.str.contains('Novelis')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150') & compositions_data.index.str.contains('Rivet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Kelly') & compositions_data.index.str.contains('Rivet')]

        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Size Sorting')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 LIBS')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Cast Removal Wrought')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Dis1 Sheet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Dis2 Sheet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Dis3 Sheet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Ford shifted comps') & compositions_data.index.str.contains(str(year))]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Kelly shifted comps') & compositions_data.index.str.contains(str(year))]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Kelly Cast Removal Wrought')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Pure Wrought')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Isolated') & compositions_data.index.str.contains(str(year))]

        # Kelly_scrapcomp = np.array(compositions_data.loc[compositions_data.index.str.contains('Kelly')])
        # sector_scrapcomp = np.array(compositions_data.loc[compositions_data.index.str.contains('Scrap Twitch')])
        # avg_sectorscrap = (0.1549 * sector_scrapcomp[0]) + (0.2097 * sector_scrapcomp[1]) + (0.1020 * sector_scrapcomp[2]) + (0.1460 * sector_scrapcomp[3]) + (0.1017 * sector_scrapcomp[4]) + (0.0720 * sector_scrapcomp[5]) + (0.1843 * sector_scrapcomp[6]) + (0.0293 * sector_scrapcomp[7])
        
        # eolscrap_comp = (0.7 * avg_sectorscrap) + (0.3 * Kelly_scrapcomp)         # Use avg_sectorscrap for combined weighted avg. of all sectors
        # eolscrap_comp = (0.7 * sector_scrapcomp) + (0.3 * Kelly_scrapcomp)
        # eolscrap_comp = avg_sectorscrap
        # eolscrap_comp = sector_scrapcomp
        # eolscrap_comp = Kelly_scrapcomp

        # UNCOMMENT BELOW FOR ALL SEPARATE EOL STREAMS
        # eolscrap_comp = []

        # for i in scrap_list:
        #     # print(i)
        #     # eolscrap_comp.append(compositions_data.loc[compositions_data.index.str.startswith(i) & compositions_data.index.str.endswith('midrange')])
        #     eolscrap_comp.append(compositions_data.loc[compositions_data.index.str.startswith(i)])

        #####

        # print(type(eolscrap_comp))

        # print(eolscrap_comp)
        # breakpoint()

        # eolscrap_comp = pd.concat(eolscrap_comp)

        # print(eolscrap_comp)

        # print(len(eolscrap_comp.to_numpy()))
        # breakpoint()

        # add_contam = eolscrap_comp.loc[eolscrap_comp.index.str.contains('Sean Kelly')]

        # # Add contamination to EOL scrap 
        # eolscrap_comp.loc[~eolscrap_comp.index.str.contains('Sean Kelly') & ~eolscrap_comp.index.str.contains('Cont. & Pack.'), 'Cu'] += 10
        # eolscrap_comp.loc[~eolscrap_comp.index.str.contains('Sean Kelly') & ~eolscrap_comp.index.str.contains('Cont. & Pack.'), 'Fe'] += 5

        # print(eolscrap_comp.loc[~eolscrap_comp.index.str.contains('Sean Kelly'), 'Cu'])

        # print(sector_scrapcomp)
        # print(eolscrap_comp.to_numpy())

        # breakpoint()

        compositions_lower = []
        compositions_midrange = []
        compositions_upper = []

        for i in demand_list:
            compositions_lower.append(compositions_data.loc[compositions_data.index.str.endswith('lower') & compositions_data.index.str.startswith(i)])
            compositions_midrange.append(compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(i)])
            compositions_upper.append(compositions_data.loc[compositions_data.index.str.endswith('upper') & compositions_data.index.str.startswith(i)])

        compositions_midrange.append(compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith('Mixed')])

        compositions_lower = pd.concat(compositions_lower)
        compositions_midrange = pd.concat(compositions_midrange)
        compositions_upper = pd.concat(compositions_upper)

        # print(eolscrap_comp.to_numpy())
        # breakpoint()


        # ford_lowCu_comp = [98.325, 0.10, 0.6, 0.075, 0.75, 0.15, 0.05]
        start_comp = np.vstack([eolscrap_comp.to_numpy(), compositions_midrange.to_numpy()])

        # print(start_comp)
        # print(len(start_comp))
        # breakpoint()

        # CHANGE THIS to set composition of starting scrap (can get as output from Scheil calculation)
        
        # start_comp = np.array([[97.4, 0.245, 1.465, 0.155, 0.58, 0.16]])
        # start_comp = np.array([0.0, 0.0])
        # Al, Cu, Mg, Mn, Si, Fe, Zn


        # Primary material compositions
        # prim_comp = compositions_data.loc[compositions_data.index.str.contains('Primary') & ~compositions_data.index.str.contains(scenario_row["prim_comp"].values[0])].to_numpy()

        # print(prim_comp)
        # breakpoint()

        if year > 2025 and (scenario == "P0610" or scenario == "P0404" or scenario == "P0303" or scenario == "P0000"):
            prim_al_comp = compositions_data.loc[compositions_data.index.str.contains('Primary') & compositions_data.index.str.contains(scenario_row["prim_comp"].values[0])].to_numpy()
        else:
            prim_al_comp = compositions_data.loc[compositions_data.index.str.contains('Primary') & compositions_data.index.str.contains("P1020")].to_numpy()
        
        prim_alloying_comps = compositions_data.loc[compositions_data.index.str.contains('Primary') & ~compositions_data.index.str.contains('Al')].to_numpy()
        prim_comp = np.vstack([prim_al_comp, prim_alloying_comps])
        # print(compositions_data.loc[compositions_data.index.str.contains('Primary')])

        # alpha_furnace = np.array([0])
        # alpha_furnace = np.full(num_products, 0.5)
        # alpha_furnace = np.full(num_products, 0)
        # alpha_furnace = np.array([0, 0, 0, 0])                  # CHANGE THIS to set weight fraction for furnace constraint FOR EACH PRODUCT


        # Search for index of specific temperature
        # temp_index = np.logical_and(temperature > 583, temperature < 584)                           # CHANGE THIS to set temperature
        # temp_index = 54

        # Determine compositions of separated supplies
        # solcomp = np.array(data.loc[temp_index, 'solcomp_al':'solcomp_fe']).flatten() * 100         # Extracts values for composition of solid phases at given temp
        solcomp = separation_data[['solcomp_' + ele.lower() for ele in elements_list]].to_numpy() * 100
        liqcomp = separation_data[['solcomp_' + ele.lower() for ele in elements_list]].to_numpy() * 100

        # solcomp = separation_data[['solcomp_al', 'solcomp_cu', 'solcomp_mg', 'solcomp_mn', 'solcomp_si', 'solcomp_fe', 'solcomp_zn']].to_numpy() * 100

        # liqcomp = np.array(data.loc[temp_index, 'liqcomp_al':'liqcomp_fe']).flatten() * 100         # Extracts values for composition of liquid phase at given temp
        # liqcomp = separation_data[['liqcomp_al', 'liqcomp_cu', 'liqcomp_mg', 'liqcomp_mn', 'liqcomp_si', 'liqcomp_fe', 'liqcomp_zn']].to_numpy() * 100

        fractions = separation_data[['mass_solid', 'mass_liquid']].to_numpy()       # Mass fractions of liquid and solid at set separation temperature

        if scrapsep_index.size != 0:
            SS_comp = np.hstack((solcomp,liqcomp)).reshape(-1,num_elements)
            scrap_comp = np.concatenate((start_comp, SS_comp))
        else:
            scrap_comp = start_comp


        # Demand composition lower ranges
        comp_lower = compositions_lower.to_numpy()      

        # Demand composition upper ranges
        comp_upper = compositions_upper.to_numpy()

        # Emissions data
        emissions_frozen = extract_emissions_data(emissions_data_frozen, year)
        emissions_moderate = extract_emissions_data(emissions_data_moderate, year)
        emissions_aggressive = extract_emissions_data(emissions_data_aggressive, year)

        # print(compositions_lower)
        # print(compositions_midrange)
        # print(compositions_upper)

        # print((sum(eol_supply)))

        # print((sum(eol_supply) * sheet_from_all_conversion)/ (collection_rate * scrap_proc_yield))

        # print(start_comp)
        # print(scrap_comp)
        # print(comp_lower)
        # print(comp_upper)

        # print(demand)
        # print(supply)
        # print(eol_supply[0])
        # print(sum(eol_supply))

        # print(prim_comp)
        # print(len(prim_comp))

        # print(supply)

        # breakpoint()


        # Al, Cu, Mg, Mn, Si, Fe, Zn
        #########################################################################################################################################################################################
        """Set up decision variables for optimization"""
        #########################################################################################################################################################################################

        # Quantity of primary material to be used for each product
        primary = m.addVars(num_products, len(prim_comp), lb=0, vtype=GRB.CONTINUOUS, name="Primary Material")
        # (:,0) = Al; (:,1) = Cu; (:,2) = Mg; (:,3) = Mn; (:,4) = Si; (:,5) = Fe; (:,6) = Zn

        scrap = m.addVars(num_products, scrap_source, lb=0, vtype=GRB.CONTINUOUS, name="Scrap Material")
        # scrap = m.addVars(num_products, scrap_source, lb=, vtype=GRB.CONTINUOUS, name="Scrap Material")
        # (0,0) = scrap source 1 used for product 1; (0,1) = scrap source 2 used for product 1; etc.

        unsep = m.addVars(num_scrap, lb=0, vtype=GRB.CONTINUOUS, name="Scrap Supply TOTAL")
        # total quantity of each unseparated scrap source

        if scrapsep_index.size > 0:
            sep = m.addVars(num_sep//2, 2, lb=0, vtype=GRB.CONTINUOUS, name="Separated Supply TOTAL")
            # total quantity of each separated scrap source

        secondary_virgin = m.addVars(num_products, len(prim_comp), lb=0, vtype=GRB.CONTINUOUS, name="Virgin Material for Secondary Production")

        secondary = m.addVars(num_products, lb=0, vtype=GRB.CONTINUOUS, name="Secondary Material Total")

        # # Binary variable that determines if a temperature is selected or not
        # opt_temp = m.addVars(num_temps, lb=0, vtype=GRB.BINARY, name="Optimal Temperature")

        # # opt_temp_index = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Temperature Index")

        # # opt_fraction = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Fraction Set")

        # opt_solcomp = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Solid Composition")

        # opt_liqcomp = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Liquid Composition")

        # print(range(len(solcomp)))
        # print(range(len(fractions)))
        # print(range(len(separation_data.index)))

        # test = np.array([[1, 2],
        #                  [3, 4]])
        # for i in range(num_scrap, num_sep + num_scrap):
        #     print(i)

        # print(num_scrap)
        

        #########################################################################################################################################################################################
        """Constraints"""
        #########################################################################################################################################################################################

        # Supply constraint
        m.addConstr(scrap.sum() <= np.sum(supply), "Total supply constraint")           # All scrap to be used can't exceed the total amount of scrap available

        # Additional supply constraints for separation
        m.addConstrs((unsep[i] == supply[i] for i in nonsep_index), "Secondary supply constraint (not separated)")                        # For the scrap sources not separated (i.e. manufacturing scrap), the unsep variable is set to supply quantity at relevant indices
        if scrapsep_index.size > 0:
            m.addConstrs((unsep[i] + sep.sum() == supply[i] for i in scrapsep_index), "Secondary supply constraint (separated)")         # Total amount of scrap is balanced for separated scrap stream
            # This constraint won't work if multiple original scrap streams are to be separated

        # Quantity of each scrap used for all prods <= total amount of each scrap
        m.addConstrs((scrap.sum("*",i) <= unsep[i] for i in range(num_scrap)), "Used scrap supply constraint (not separated)")                # Unseparated scrap sources are the first num_scrap scrap sources
        m.addConstrs((scrap.sum("*",i) <= furnace_yield * sep[((i - num_scrap)//2),((i - num_scrap)%2)] for i in range(num_scrap, scrap_source)), "Used scrap supply constraint (separated)")         # Separated scrap sources are the remaining scrap sources
        # m.addConstrs((scrap.sum("*",i) <= sep[((i - num_scrap)//2),((i - num_scrap)%2)] for i in range(num_scrap, scrap_source)), "Used scrap supply constraint (separated)")         # Separated scrap sources are the remaining scrap sources


        # Demand constraint
        m.addConstrs(((primary.sum(i,"*") + secondary[i]) * furnace_yield >= demand[i] for i in range(num_products)), "Demand constraint")
        # m.addConstrs((primary.sum(i,"*") + secondary[i] >= demand[i] for i in range(num_products)), "Demand constraint")
            # 0.95 = 5% melt loss

        # Secondary production of each product is the sum of all scrap in each product and virgin material in each product
        m.addConstrs((scrap.sum(i,"*") + secondary_virgin.sum(i, "*") == secondary[i] for i in range(num_products)), "Secondary production quantity sum")

        # Mass fractions constraint
        # m.addConstr(opt_temp.sum() == 1, "Single temperature choosing constraint")           # Choose only one temp
        # m.addConstr((opt_temp_index == sum(opt_temp[i] * temp_indices[i] for i in range(num_temps))), "Single temperature indexing constraint")

        # opt_fraction = sum(fractions[i] * opt_temp[i] for i in range(num_temps))        # Sum effectively removes zero values

        # m.addConstrs((fractions[i] * (supply[j] - unsep[j]) == sep[j,i] for i in range(num_sep) for j in scrapsep_index), "Mass fractions constraint")
        # print(scrapsep_index.size)
        if scrapsep_index.size > 0:
            m.addConstrs((fractions[j][i] * (supply[k] - unsep[k] - sum(sep.sum(l,"*") for l in range(num_temps) if l != j)) == sep[j,i] for i in range(2) for j in range(num_temps) for k in scrapsep_index), "Mass fractions constraint")

        # Lower composition constraint for primary production
        m.addConstrs((sum(prim_comp[l][k] * primary[i,l] for l in range(len(prim_comp))) >= (comp_lower[i][k] * primary.sum(i,"*")) for i in range(num_products) for k in range(num_elements)), "Lower composition constraint for primary material")
            # Concentration (100% element k) * quantity of element k primary used for product i >= lower comp limit of element k used for product i * all primary material used for product i

        # Upper composition constraint for primary production
        m.addConstrs((sum(prim_comp[l][k] * primary[i,l] for l in range(len(prim_comp))) <= (comp_upper[i][k] * primary.sum(i,"*")) for i in range(num_products) for k in range(num_elements)), "Upper composition constraint for primary material")


        # opt_solcomp = sum(solcomp[i] * opt_temp[i] for i in range(num_temps))
        # opt_liqcomp = sum(liqcomp[i] * opt_temp[i] for i in range(num_temps))
        # SS_comp = np.array([opt_solcomp,
        #                     opt_liqcomp])

        # SS_comp = np.hstack((solcomp,liqcomp)).reshape(-1,7)

        # scrap_comp = start_comp

        # scrap_comp = np.concatenate((start_comp, SS_comp))

        # Lower composition constraint for secondary production
        m.addConstrs((sum(prim_comp[l][k] * secondary_virgin[i,l] for l in range(len(prim_comp))) + sum(scrap_comp[j][k] * scrap[i,j] for j in range(scrap_source)) >= (comp_lower[i][k] * secondary[i]) for i in range(num_products) for k in range(num_elements)), "Lower composition constraint for secondary material")
            # 100 * sec_virg[prod1, Al] + (scrap_comp[source1][Al] * scrap[prod1, source1] scrap_comp[source2][Al])


        # Upper composition constraint for secondary production
        m.addConstrs((sum(prim_comp[l][k] * secondary_virgin[i,l] for l in range(len(prim_comp))) + sum(scrap_comp[j][k] * scrap[i,j] for j in range(scrap_source)) <= (comp_upper[i][k] * secondary[i]) for i in range(num_products) for k in range(num_elements)), "Upper composition constraint for secondary material")


        # Furnace constraint for secondary production
        m.addConstrs((alpha_furnace[i] * secondary[i] <= scrap.sum(i,"*") for i in range(num_products)), "Furnace charge constraint")
        # If secondary is under (furnace) percentage of scrap, use for primary instead of secondary


        # # Manually force closed loop reuse of scrap into original source
        # for i in range(0,6):
        #     for j in range(1,8):
        #          if j != i+1:
        #             m.addConstr(scrap[i,j] == 0)

        # m.addConstrs((scrap.sum("*",i) >= 0.3 * sum(scrap.sum("*",j) for j in range(num_scrap, scrap_source)) for i in range(num_scrap, scrap_source)), "Minimum separated scrap used constraint")
        # m.addConstrs((scrap.sum("*",i) >= 0.3 * sum(scrap.sum("*",i)) for i in range(num_scrap, scrap_source)), "Minimum separated scrap used constraint")


        m.ModelSense = GRB.MINIMIZE
        m.params.NonConvex = 2

        # Set objective
        if objective == "primary":
            m.setObjective(primary.sum() + secondary_virgin.sum(), GRB.MINIMIZE)
        # m.setObjective(8510000 * primary.sum("*", 0) + 8510000 * secondary_virgin.sum("*", 0) + 30000 * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + 513000 * secondary.sum(), GRB.MINIMIZE)
        # m.setObjective(prim_al_emissions * primary.sum("*", 0) + prim_cu_emissions * primary.sum("*", 1) + prim_mg_emissions * primary.sum("*", 2) + prim_mn_emissions * primary.sum("*", 3) + prim_si_emissions * primary.sum("*", 4) + prim_fe_emissions * primary.sum("*", 5) + \
        #                prim_al_emissions * secondary_virgin.sum("*", 0) + prim_cu_emissions * secondary_virgin.sum("*", 1) + prim_mg_emissions * secondary_virgin.sum("*", 2) + prim_mn_emissions * secondary_virgin.sum("*", 3) + prim_si_emissions * secondary_virgin.sum("*", 4) + prim_fe_emissions * secondary_virgin.sum("*", 5) + \
        #                scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
        #                sec_al_emissions * secondary.sum() + \
        #                fc_emissions * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))), GRB.MINIMIZE)
        # m.setObjective(prim_al_emissions * primary.sum("*", 0) + prim_al_emissions * secondary_virgin.sum("*", 0) + scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + sec_al_emissions * secondary.sum() + 0 * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))), GRB.MINIMIZE)
        elif objective == "emissions":
            # m.setObjective(prim_al_emissions * primary.sum("*", 0) + prim_cu_emissions * primary.sum("*", 1) + prim_mg_emissions * primary.sum("*", 2) + prim_mn_emissions * primary.sum("*", 3) + prim_si_emissions * primary.sum("*", 4) + prim_fe_emissions * primary.sum("*", 5) + prim_zn_emissions * primary.sum("*", 6) + \
            #                prim_al_emissions * secondary_virgin.sum("*", 0) + prim_cu_emissions * secondary_virgin.sum("*", 1) + prim_mg_emissions * secondary_virgin.sum("*", 2) + prim_mn_emissions * secondary_virgin.sum("*", 3) + prim_si_emissions * secondary_virgin.sum("*", 4) + prim_fe_emissions * secondary_virgin.sum("*", 5) + prim_zn_emissions * secondary_virgin.sum("*", 6) + \
            #                scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
            #                sec_al_emissions * secondary.sum() + \
            #                fc_emissions * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))), GRB.MINIMIZE)
            print("emissions optimizing not functional currently")

        # Emissions factors:
            # Primary aluminum: 8.51 kgCO2eq/kg-out = 8.51e6 kgCO2eq/kt-out
            # Scrap preparation (for EOL scrap): 0.03 kgCO2eq/kg-out = 3e4 kgCO2eq/kt-out
            # Secondary aluminum: 0.513 kgCO2eq/kg-out = 5.13e5 kgCO2eq/kt-out

        m.optimize()
        # print (m.display())

        output = {}
        # breakpoint()

        # emissions = m.getObjective().getValue()
        # emissions = (prim_al_emissions * primary.sum("*", 0) + prim_cu_emissions * primary.sum("*", 1) + prim_mg_emissions * primary.sum("*", 2) + prim_mn_emissions * primary.sum("*", 3) + prim_si_emissions * primary.sum("*", 4) + prim_fe_emissions * primary.sum("*", 5) + prim_zn_emissions * primary.sum("*", 6) + \
        #              prim_al_emissions * secondary_virgin.sum("*", 0) + prim_cu_emissions * secondary_virgin.sum("*", 1) + prim_mg_emissions * secondary_virgin.sum("*", 2) + prim_mn_emissions * secondary_virgin.sum("*", 3) + prim_si_emissions * secondary_virgin.sum("*", 4) + prim_fe_emissions * secondary_virgin.sum("*", 5) + prim_zn_emissions * secondary_virgin.sum("*", 6) + \
        #              scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
        #              sec_al_emissions * secondary.sum() + \
        #              fc_emissions * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
        #              rolling_emissions * 0.66 * (sum(demand)) + \
        #              blanking_stamping_emissions * 0.66 * 0.95 * 0.70 * (sum(demand)) + \
        #              assembly_emissions * 0.66 * 0.95 * 0.70 * 0.995 * (sum(demand))).getValue() / 1000000   # Rolling, Blanking, Stamping, and Assembly emissions
        # print("Emissions (kgCO2eq/kg-in): %g \n" % emissions)

        emissions_frozen_total = calculate_total_emissions(emissions_frozen, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap)
        emissions_moderate_total = calculate_total_emissions(emissions_moderate, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap)
        emissions_aggressive_total = calculate_total_emissions(emissions_aggressive, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap)

        print("Emissions Frozen (kgCO2eq/kg-in): %g" % emissions_frozen_total)
        print("Emissions Moderate (kgCO2eq/kg-in): %g" % emissions_moderate_total)
        print("Emissions Aggressive (kgCO2eq/kg-in): %g \n" % emissions_aggressive_total)

        # print(sum(demand))
        # breakpoint()

        # downstream_emissions = (411000 * (sum(demand)) + \
        #                         11000 * 0.66 * (sum(demand)) + \
        #                         36000 * 0.66 * 0.95 * (sum(demand)) + \
        #                         250000 * 0.66 * 0.95 * 0.70 * (sum(demand))) / 1000000

        # print("Downstream emissions: %g \n" % downstream_emissions)

        # downstream_emissions_2 = (623000 * 0.66 * (sum(demand)) + \
        #                         12000 * 0.66 * 0.95 * (sum(demand)) + \
        #                         52000 * 0.66 * 0.95 * 0.70 * (sum(demand)) + \
        #                         251000 * 0.66 * 0.95 * 0.70 * 0.995 * (sum(demand))) / 1000000

        # print("Downstream emissions 2: %g \n" % downstream_emissions_2)


        # breakpoint()

        # print(m.getObjective())

        for v in m.getVars():
            # print('%s %g' % (v.varName, v.x))
            output[v.varName] = [v.x]
            # print(v.ConstrName)

        print(output)

        Cnames = m.getAttr('constrName', m.getConstrs())

        prim_preML = primary.sum().getValue()
        prim_postML = 0.95 * primary.sum().getValue()

        # print(primary)

        print("Total amount of primary virgin material used (before melt loss): %g" % prim_preML)
        print("Total amount of primary virgin material used (post melt loss): %g \n" % prim_postML)

        secvirg_preML = secondary_virgin.sum().getValue()
        secvirg_postML = 0.95 * secondary_virgin.sum().getValue()

        print("Total amount of virgin material in secondary furnace used (before melt loss): %g" % secvirg_preML)
        print("Total amount of virgin material in secondary furnace used (post melt loss): %g \n" % secvirg_postML)

        eol_scrapsum_RR = ((sum(scrap.sum("*", j) for j in range(eol_scrapsources.shape[0]))) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source)) / furnace_yield).getValue()
        eol_scrapsum_RC = ((sum(scrap.sum("*", j) for j in range(eol_scrapsources.shape[0]))) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source)) / furnace_yield).getValue() * furnace_yield
    
        if 'EOL cap' in folder:
            if eol_scrapsum_RR > ((sum(eol_supply)) * sheet_from_all_conversion):
                eol_scrapsum_RR = ((sum(eol_supply)) * sheet_from_all_conversion)
                eol_scrapsum_RC = eol_scrapsum_RR * furnace_yield
                print("EOL scrap cap applied")
        
        totaleol = (sum(eol_supply) * sheet_from_all_conversion)/ (collection_rate * scrap_proc_yield)

        print("Total EOL scrap used (before melt loss): %g" % eol_scrapsum_RR)
        print("Total EOL scrap used (post melt loss): %g" % eol_scrapsum_RC)
        print("Total EOL scrap collected (assume 0.95 collection rate): %g" % (totaleol * 0.95))
        print("Total EOL scrap available: %g \n" % totaleol)


        manuf_scrapsum_RR = ((sum(scrap.sum("*", i) for i in range(eol_scrapsources.shape[0], num_scrap)))).getValue()
        manuf_scrapsum_RC = ((sum(scrap.sum("*", i) for i in range(eol_scrapsources.shape[0], num_scrap)))).getValue() * furnace_yield
        totalmanuf = sum(manuf_supply)

        print("Total manufacturing scrap used (before melt loss): %g" % manuf_scrapsum_RR)
        print("Total manufacturing scrap used (post melt loss): %g" % manuf_scrapsum_RC)
        print("Total manufacturing scrap available: %g \n" % totalmanuf)

        scrapsum_RR = eol_scrapsum_RR + manuf_scrapsum_RR
        scrapsum_RC = eol_scrapsum_RC + manuf_scrapsum_RC
        # totaleol = (supply_data.loc[~supply_data['Sector'].str.startswith('All')])[year].to_numpy()
        
        totalsupply = totaleol + sum(manuf_supply)
        print("Total amount of scrap used (before melt loss): %g" % scrapsum_RR)
        print("Total amount of scrap used (post melt loss): %g" % scrapsum_RC)
        print("Total amount of scrap available: %g \n" % totalsupply)

        totaldemand = sum(demand)

        # print("Total demand: %g" % totaldemand)

        RR = scrapsum_RR/totalsupply
        RC = scrapsum_RC/totaldemand

        RR_EOL = eol_scrapsum_RR/totaleol
        RC_EOL = eol_scrapsum_RC/totaldemand

        RR_manuf = manuf_scrapsum_RR/totalmanuf
        RC_manuf = manuf_scrapsum_RC/totaldemand

        print("Total demand: ", totaldemand, "\n")

        print("RR: ", RR)
        print("RC: ", RC)
        print("EOL RR: ", RR_EOL)
        print("EOL RC: ", RC_EOL)
        print("Manufacturing RR: ", RR_manuf)
        print("Manufacturing RC: ", RC_manuf)


        # print("Separated total: ", sep.sum().getValue())

        # print(demand)

        # print("Optimal separation temperature: %g" % temperature[opt_temp_index.x])

        # # print(num_sep)
        # # print(num_scrap)
        # # print("Test here %g" % (range(num_scrap)))
        # for i in range(scrap_source):
        #     print("scrap sum: %g" % (scrap.sum("*",i)).getValue())
        # # for i in scrapsep_index:
        # #     print(i)

        # # print(primary.sum(0,"*"))

        # # print(SS_comp)

        # print(unsep)

        ## UNCOMMENT UP TO "SIGN" LINE TO ADD CONSTRAINTS 
        ## NOTE: for some reason doesn't work with demand shift 5x -> 6x U.S. ABS 2036

        ranked_constraints_df = rank_filtered_constraints_by_objective_impact(m, demand_list, elements_list)

        # slack = np.around(m.slack,2)

        # # # SAObjLow = m.SAObjLow       # Minimum objective function coefficient values (to retain optimal solution)
        # # # SAObjUp = m.SAObjUp         # Maximum objective function coefficient values (to retain optimal solution)
 
        # SARHSLow = np.around(m.SARHSLow,2)      # Minimum RHS values (to retain optimal solution)
        # SARHSUp = np.around(m.SARHSUp,2)        # Maximum RHS values (to retain optimal solution)

        # shadow = m.Pi               # Shadow prices

        # # print(shadow)

        # sign = m.sense

        # print(sign)

        # # Convert constraint data into dataframe
        # df = pd.DataFrame(columns=Cnames)
        # df.loc[0] = slack
        # df.loc[1] = shadow
        # df.loc[2] = sign
        # df.loc[3] = SARHSLow
        # df.loc[4] = SARHSUp

        # breakpoint()

        # Construct column labels
        primary_virgin_labels = ['Primary virgin ' + ele for ele in elements_list]
        secondary_virgin_labels = ['Secondary virgin ' + ele for ele in elements_list]
        eol_scrap_labels = [eolscrap for eolscrap in scrap_list]
        manufacturing_scrap_labels = [dem + ' manufacturing scrap' for dem in demand_list]
        if to_mix.size != 0:
            mixed_manufacturing_labels = [f'Mixed {to_mix} manufacturing scrap']
        else:
            mixed_manufacturing_labels = []
        if scrapsep_index.size > 0:
            separated_stream_labels = [f'Separated stream {i//2, i%2}' for i in range(num_sep)]
        else:
            separated_stream_labels = []

        columns = (primary_virgin_labels + secondary_virgin_labels + eol_scrap_labels +
                   manufacturing_scrap_labels + mixed_manufacturing_labels + separated_stream_labels)

        # print("Final Columns Labels:", columns)

        # Initialize DataFrame to hold the results
        num_rows = len(demand_list)
        num_cols = len(columns)
        table = pd.DataFrame(np.zeros((num_rows, num_cols)), index=demand_list, columns=columns)

        # print("Table initialized with zeros:", table)

        # Fill in the table with results from the dictionary
        for key, value in output.items():
            # Check if 'value' is a list and has exactly one element
            if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (int, float)):
                value = value[0]  # Extract the single float from the list
            
            if isinstance(value, (int, float)):  # Ensure extracted value is a number
                if 'Primary Material' in key:
                    i, j = map(int, key[len('Primary Material') + 1 : -1].split(','))
                    table.iat[i, j] = value
                elif 'Virgin Material for Secondary Production' in key:
                    i, j = map(int, key[len('Virgin Material for Secondary Production') + 1 : -1].split(','))
                    j += len(elements_list)
                    table.iat[i, j] = value
                elif 'Scrap Material' in key:
                    i, j = map(int, key[len('Scrap Material') + 1 : -1].split(','))
                    j += 2 * len(elements_list)
                    table.iat[i, j] = value
            else:
                print(f"Skipping key '{key}' with unexpected value type or structure: {value}")

        # Ensure indices are valid
        # print("Table after processing:", table)

        # Calculate row sums and append as a new column
        row_sums = table.sum(axis=1)
        table['Row Total'] = row_sums
        # print("Row sums calculated and added to table.")

        # Calculate 'Total Demand (adjusted for melt yield)'
        table['Total Demand (adjusted for melt yield)'] = row_sums * furnace_yield
        # print("Total Demand (adjusted for melt yield) calculated and added to table.")

        # Calculate and append column sums (including new columns)
        col_sums = table.sum(axis=0)
        # print("Column sums calculated.")
        # print(table)
        # print(col_sums)

        # Create a new row for column totals, with NaN for 'Row Total' and 'Total Demand'
        sum_row = pd.Series(np.append(col_sums.values[:-2], [np.nan, np.nan]), index=table.columns)
        # print(sum_row)

        # Append the new row to the table
        table = pd.concat([table, sum_row.to_frame().T], ignore_index=True)
        # print("New row appended to the table.")

        # Set the index label for the new row
        table.index = list(demand_list) + ['Column Total']

        # Display the final table
        # print("Final Table with Total Demand Adjustment:")
        # print(table)

        # DMFA INPUTS sheet
        # Create DataFrame for "Demand alloys"
        demand_alloys_df = pd.DataFrame({
            'Demand alloys': demand_list,
            str(year): demand
        })

        # Combine all scrap labels
        scrap_sources_labels = eol_scrap_labels + manufacturing_scrap_labels + mixed_manufacturing_labels
        
        # Adjust only the eol_scrap part of supply by dividing by 0.95
        adjusted_supply = supply.copy()  # Create a copy to preserve original data
        adjusted_supply[:len(eol_scrap_labels)] /= (collection_rate * scrap_proc_yield)  # Only adjust the eol_scrap part

        # Create DataFrame for "Scrap sources"
        scrap_sources_df = pd.DataFrame({
            'Scrap sources': scrap_sources_labels,
            str(year): adjusted_supply
        })

        # GUROBI OUTPUTS RAW sheet
        gurobi_output_df = pd.DataFrame(output)

        # INPUT SCRAP COMPS sheet
        scrap_comps_df = pd.DataFrame(start_comp, index=scrap_sources_labels, columns=elements_list)

        if snapshot == True:
            filepath = folder + str(year) + '_domesticABS_' + label + '.xlsx'

            # Set up ExcelWriter
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                
                # OUTPUTS sheet
                table.to_excel(writer, sheet_name='OUTPUTS')

                # RANKED CONSTRAINTS sheet
                ranked_constraints_df.to_excel(writer, sheet_name='RANKED CONSTRAINTS', index=False)

                # DMFA INPUTS sheet
                demand_alloys_df.to_excel(writer, sheet_name='DMFA INPUTS', index=False, startrow=0)
                scrap_sources_df.to_excel(writer, sheet_name='DMFA INPUTS', index=False, startrow=len(demand_list) + 3)

                # INPUT SCRAP COMPS sheet
                scrap_comps_df.to_excel(writer, sheet_name='INPUT SCRAP COMPS')

                # GUROBI OUTPUTS RAW sheet
                gurobi_output_df.to_excel(writer, sheet_name='GUROBI OUTPUTS RAW', index=False)

            print("Excel file created successfully with five sheets.")

        # ## save to xlsx file

        # filepath = r'/Users/alissatsai/Documents/Fall 2024 University of Michigan/Aluminum optimization/Domestic ABS Results/Separate Manufacturing Scrap/2035_domesticABS_sepman_constraints.xlsx'

        # df.to_excel(filepath, index=False)


        # # Convert quantity data into dataframe
        # df = pd.DataFrame(output)

        # ## save to xlsx file

        # filepath = r'/Users/alissatsai/Documents/Fall 2024 University of Michigan/Aluminum optimization/Domestic ABS Results (min prim)/Shifting 5x to 6x Demand/2035_domesticABS_shiftingdemand_raw.xlsx'

        # df.to_excel(filepath, index=False)


        return(prim_preML, secvirg_preML, eol_scrapsum_RR, manuf_scrapsum_RR, RR, RC, RR_EOL, RC_EOL, RR_manuf, RC_manuf, emissions_frozen_total, emissions_moderate_total, emissions_aggressive_total)

    except gp.GurobiError as e:
        print('Error code ' + str(e.errno) + ': ' + str(e))

    except AttributeError:
        print('Encountered an attribute error')

def optimize_ABS_LIBS(objective, year, scenario, snapshot, folder):
    try:
        # Create a new model
        m = gp.Model("Al-opt")

        # Read and process data
        scenarios_references = pd.read_excel('domesticABS_scenarios.xlsx', index_col=[0], sheet_name="Table")
        scenario_row = scenarios_references.loc[[scenario]]
        baseline_row = scenarios_references.loc[["Baseline"]]
        label = scenario_row["label"].values[0]

        separation_data_file = scenario_row["separation_data_file"].values[0]
        demand_dict_file = scenario_row["demand_dict_file"].values[0]
        compositions_dict_file = scenario_row["compositions_dict_file"].values[0]
        supply_data_file = scenario_row["supply_data_file"].values[0]
        emissions_data_file_frozen = scenario_row["emissions_data_file_frozen"].values[0]
        emissions_data_file_moderate = scenario_row["emissions_data_file_moderate"].values[0]
        emissions_data_file_aggressive = scenario_row["emissions_data_file_aggressive"].values[0]
        product_parameters_dict_file = scenario_row["product_parameters_dict_file"].values[0]

        separation_data = pd.read_excel(separation_data_file)                                               # *FUTURE IMPROVEMENT* Can integrate Scheil calculation into this program
        demand_dict = pd.read_excel(demand_dict_file, sheet_name=None)
        compositions_dict = pd.read_excel(compositions_dict_file, sheet_name=None)                          # Use this for all shape scenarios
        supply_data = pd.read_excel(supply_data_file)                                                       # Use this for only sheet scenarios
        # supply_dict = pd.read_excel('100CR_sep_domestic_Al_supply.xlsx', sheet_name=None)                 # Use this for all shape scenarios
        emissions_data_frozen = pd.read_excel(emissions_data_file_frozen)
        emissions_data_moderate = pd.read_excel(emissions_data_file_moderate)
        emissions_data_aggressive = pd.read_excel(emissions_data_file_aggressive)
        product_parameters_dict = pd.read_excel(product_parameters_dict_file, sheet_name=None)

        demand_data = pd.concat(demand_dict.values(), ignore_index=True)                                    
        compositions_data = pd.concat(compositions_dict.values(), ignore_index=True)                      # Use this for all shape scenarios
        # supply_data = pd.concat(supply_dict.values(), ignore_index=True)                                  # Use this for all shape scenarios
        product_parameters = pd.concat(product_parameters_dict.values(), ignore_index=True)

        compositions_data.set_index("Alloy", inplace=True, drop=True)
        # emissions_data.set_index("Process", inplace=True, drop=True)
        product_parameters.set_index("Alloy", inplace=True, drop=True)
        demand_list = demand_data['Alloy'].to_numpy()

        emissions_data_frozen.set_index("Process", inplace=True, drop=True)
        emissions_data_moderate.set_index("Process", inplace=True, drop=True)
        emissions_data_aggressive.set_index("Process", inplace=True, drop=True)

        compositions_data = compositions_data.drop(columns=['Al'])              # Removes Al column from compositions data

        #########################################################################################################################################################################################
        """Initialize and set variables to be used later in optimization"""
        #########################################################################################################################################################################################
        num_proc = 1                       # Default 1 separation process for 1 singular LIBS process 

        # Set number of products
        num_products = demand_data.shape[0]                            # CHANGE THIS depending on number of products

        # Set number of elements
        # num_elements = 6                            # CHANGE THIS depending on number of elements (including Al)
        # # Al, Cu, Mg, Mn, Si, Fe
        num_elements = len(compositions_data.columns)                              # Number of elements changes depending on the number of columns (alloying elements)
        # Al, Cu, Mg, Mn, Si, Fe, Zn

        elements_list = compositions_data.columns.values

        furnace_yield = 0.95

        collection_rate = 0.95

        scrap_proc_yield = scenario_row["scrap_proc_yield"].values[0]                       # Default value of 0.95 (from aluminum production research)
        # scrap_proc_yield = 0.7

        # Check supply_data Dataframe if "Fraction" row is listed
        fraction_row = supply_data.loc[supply_data.iloc[:, 0].str.startswith('Fraction')]
        if not fraction_row.empty:
            sheet_from_all_conversion = supply_data.loc[supply_data.iloc[:, 0].str.startswith('Fraction')][year].to_numpy()[0]      # Dataframe of fraction of ABS of Twitch from supply data sheet (use when supply is all Twitch)
        else:
            sheet_from_all_conversion = 1.0
        # sheet_from_all_conversion = 0.473372676098014

        # eol_scrapsources = supply_data.loc[supply_data['Sector'].str.startswith('All')]         # For all scrap streams combined
        # eol_scrapsources = supply_data.loc[~supply_data['Sector'].str.startswith('All')]        # For all separate streams
        eol_scrapsources = supply_data.loc[~supply_data.iloc[:, 0].str.startswith('Fraction')]                                                      # Use this when all scrap sources in supply_data are to be used
        
        scrap_list = eol_scrapsources.iloc[:, 0].to_numpy()


        # Set number of scrap sources BEFORE separation
        num_mixed = scenario_row["num_mixed"].values[0].item()
        num_scrap = eol_scrapsources.shape[0] + num_products + num_mixed
        # num_scrap = eol_scrapsources.shape[0] + num_products + 1
            # EOL scrap stream + manufacturing scrap for each product + mixed 5xxx 6xxx
        # num_scrap = eol_scrapsources.shape[0] + num_products
            # EOL scrap stream + manufacturing scrap for each product

        
        # breakpoint()
        

        # Set index of scrap source being separated
        # scrapsep_index = np.array([0])              # CHANGE THIS depending on which source(s) are being separated
        # scrapsep_index = np.array([])

        if pd.isna(scenario_row["scrapsep_index"].values[0]):
            scrapsep_index = np.array([])
        else:
            scrapsep_index = np.array([scenario_row["scrapsep_index"].values[0].astype(int).item()])

        scrap_indices = np.arange(num_scrap)
        nonsep_index = scrap_indices[~np.isin(scrap_indices, scrapsep_index)]

        if scrapsep_index.size > 0:
            num_sep = 3*num_proc
        else:
            num_sep = 0

        # Calculate total number of potential scrap sources
        scrap_source = num_scrap + num_sep                              # This can also be manually changed; total # of scrap sources (including original)

        # print(scrap_source)
        # breakpoint()

        # Values
        process_yield = []
        alpha_furnace = []


        for i in demand_list:
            process_yield.append(product_parameters.query('Alloy== @i')["Process Yield"])
            alpha_furnace.append(product_parameters.query('Alloy== @i')["Furnace Charge Constraint"])
        # Obtains process yields and furnace charge constraint values for each specified product

        process_yield = pd.concat(process_yield).to_numpy()
        alpha_furnace = pd.concat(alpha_furnace).to_numpy()

        RR_CL = 0.91
        # RR_CL = 1
        demand = (demand_data[year].to_numpy())/process_yield

        if not scenario_row["scrap_proc_yield in DMFA"].values[0]:
            eol_supply = eol_scrapsources[year].to_numpy() * scrap_proc_yield             # Use when scrap processing yield not built into DMFA supply inputs
        else:
            eol_supply = eol_scrapsources[year].to_numpy()                                  # Use when scrap processing yield built into DMFA supply
        
        # eol_supply = eol_scrapsources[year].to_numpy() * scrap_proc_yield             # Use when scrap processing yield not built in
        # eol_supply = eol_scrapsources[year].to_numpy()                                  # Use when scrap processing yield built into DMFA supply
        manuf_supply = (demand * (1-process_yield)) * RR_CL
 
        mixing = 1                                      # CHANGE THIS to specify how much of the non Ford 5xxx and 6xxx series scrap is mixed

        if pd.isna(scenario_row["to_mix"].values[0]):
            to_mix = np.array([])
        else:
            to_mix = np.array(json.loads(scenario_row["to_mix"].values[0]))

        # to_mix = np.array(["Sheet 5xxx", "Sheet 6xxx 6005C", "Sheet 6xxx 6016"])
        # to_mix = np.array([])

        to_mix_list = []
        quant_before_mix = []
        comps_to_mix = []

        manuf_supply_edited = manuf_supply.copy()

        mixed_manuf_supply = np.array([])


        # Below lines: when the to_mix array contains a list of manufacturing scrap streams to be mixed,
        # iterate through the list of scrap streams to mix together and perform a series of operations
        # that mixes the quantities of these manufacturing scrap streams together
        if to_mix.size != 0:
            for i in to_mix:
                index = demand_data.query('Alloy== @i').index[0]
                to_mix_list.append(manuf_supply[index])
                manuf_supply_edited[index] = (1 - mixing) * manuf_supply[index]
                quant_before_mix.append(manuf_supply[index])
                comps_to_mix.append(compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(i)])


            mixed_manuf_supply = np.array([mixing * (sum(to_mix_list))])

        supply = np.concatenate([eol_supply, manuf_supply_edited, mixed_manuf_supply])

        # Composition definitions

        # Below lines: when the to_mix array contains a list of manufacturing scrap streams to be mixed,
        # iterate through the list of scrap streams to mix together and perform a series of operations
        # that calculates the weight average composition of these manufacturing scrap streams, then adds
        # these new mixed manufacturing scrap stream compositions to the full list of compositions
        if to_mix.size != 0:
            comps_to_mix = pd.concat(comps_to_mix).to_numpy()
            quant_before_mix = np.array(quant_before_mix)

            if mixed_manuf_supply == 0:
                new_row = np.zeros(num_elements)
                # new_row = [0, 0, 0, 0, 0, 0]
            else:
                mixed_comp = ((comps_to_mix * quant_before_mix[:, None]).sum(axis=0)) / mixed_manuf_supply
                new_row = mixed_comp

            compositions_data.loc[len(compositions_data)] = new_row
            compositions_data.rename(index={(len(compositions_data) - 1): 'Mixed ' + str(to_mix) + ' midrange'}, inplace=True)

        eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains(scenario_row["eolscrap_comp keyword"].values[0]) & compositions_data.index.str.contains(str(year))]

        # eolscrap_comp = compositions_data.loc['Sheet Supply']
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Sean Kelly')]
            # Midrange of EOL scrap composition as defined in Kelly thesis
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150') & compositions_data.index.str.contains('UM')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150') & compositions_data.index.str.contains('Novelis')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150') & compositions_data.index.str.contains('Rivet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Kelly') & compositions_data.index.str.contains('Rivet')]

        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Size Sorting')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 LIBS')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Cast Removal Wrought')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Dis1 Sheet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Dis2 Sheet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('F-150 Dis3 Sheet')]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Kelly shifted comps') & compositions_data.index.str.contains(str(year))]
        # eolscrap_comp = compositions_data.loc[compositions_data.index.str.contains('Kelly Cast Removal Wrought')]

        # Kelly_scrapcomp = np.array(compositions_data.loc[compositions_data.index.str.contains('Kelly')])
        # sector_scrapcomp = np.array(compositions_data.loc[compositions_data.index.str.contains('Scrap Twitch')])
        # avg_sectorscrap = (0.1549 * sector_scrapcomp[0]) + (0.2097 * sector_scrapcomp[1]) + (0.1020 * sector_scrapcomp[2]) + (0.1460 * sector_scrapcomp[3]) + (0.1017 * sector_scrapcomp[4]) + (0.0720 * sector_scrapcomp[5]) + (0.1843 * sector_scrapcomp[6]) + (0.0293 * sector_scrapcomp[7])
        
        # eolscrap_comp = (0.7 * avg_sectorscrap) + (0.3 * Kelly_scrapcomp)         # Use avg_sectorscrap for combined weighted avg. of all sectors
        # eolscrap_comp = (0.7 * sector_scrapcomp) + (0.3 * Kelly_scrapcomp)
        # eolscrap_comp = avg_sectorscrap
        # eolscrap_comp = sector_scrapcomp
        # eolscrap_comp = Kelly_scrapcomp

        # UNCOMMENT BELOW FOR ALL SEPARATE EOL STREAMS
        # eolscrap_comp = []

        # for i in scrap_list:
        #     # print(i)
        #     # eolscrap_comp.append(compositions_data.loc[compositions_data.index.str.startswith(i) & compositions_data.index.str.endswith('midrange')])
        #     eolscrap_comp.append(compositions_data.loc[compositions_data.index.str.startswith(i)])

        #####

        # print(type(eolscrap_comp))

        # print(eolscrap_comp)

        # eolscrap_comp = pd.concat(eolscrap_comp)

        # print(eolscrap_comp)

        # print(len(eolscrap_comp.to_numpy()))
        # breakpoint()

        # add_contam = eolscrap_comp.loc[eolscrap_comp.index.str.contains('Sean Kelly')]

        # # Add contamination to EOL scrap 
        # eolscrap_comp.loc[~eolscrap_comp.index.str.contains('Sean Kelly') & ~eolscrap_comp.index.str.contains('Cont. & Pack.'), 'Cu'] += 10
        # eolscrap_comp.loc[~eolscrap_comp.index.str.contains('Sean Kelly') & ~eolscrap_comp.index.str.contains('Cont. & Pack.'), 'Fe'] += 5

        # print(eolscrap_comp.loc[~eolscrap_comp.index.str.contains('Sean Kelly'), 'Cu'])

        # print(sector_scrapcomp)
        # print(eolscrap_comp.to_numpy())

        # breakpoint()

        compositions_lower = []
        compositions_midrange = []
        compositions_upper = []

        for i in demand_list:
            compositions_lower.append(compositions_data.loc[compositions_data.index.str.endswith('lower') & compositions_data.index.str.startswith(i)])
            compositions_midrange.append(compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(i)])
            compositions_upper.append(compositions_data.loc[compositions_data.index.str.endswith('upper') & compositions_data.index.str.startswith(i)])

        compositions_midrange.append(compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith('Mixed')])

        compositions_lower = pd.concat(compositions_lower)
        compositions_midrange = pd.concat(compositions_midrange)
        compositions_upper = pd.concat(compositions_upper)

        # print(eolscrap_comp.to_numpy())
        # breakpoint()

        # ford_lowCu_comp = [98.325, 0.10, 0.6, 0.075, 0.75, 0.15, 0.05]
        start_comp = np.vstack([eolscrap_comp.to_numpy(), compositions_midrange.to_numpy()])

        # print(start_comp)
        # print(len(start_comp))
        # breakpoint()

        # CHANGE THIS to set composition of starting scrap (can get as output from Scheil calculation)
        
        # start_comp = np.array([[97.4, 0.245, 1.465, 0.155, 0.58, 0.16]])
        # start_comp = np.array([0.0, 0.0])
        # Al, Cu, Mg, Mn, Si, Fe, Zn


        # Primary material compositions

        prim_al_comp = compositions_data.loc[compositions_data.index.str.contains('Primary') & compositions_data.index.str.contains(scenario_row["prim_comp"].values[0])].to_numpy()
        prim_alloying_comps = compositions_data.loc[compositions_data.index.str.contains('Primary') & ~compositions_data.index.str.contains('Al')].to_numpy()
        prim_comp = np.vstack([prim_al_comp, prim_alloying_comps])
        # prim_comp = compositions_data.loc[compositions_data.index.str.contains('Primary') & ~compositions_data.index.str.contains('P1020')].to_numpy()
        # prim_comp = compositions_data.loc[compositions_data.index.str.contains('Primary') & ~compositions_data.index.str.contains('P0610')].to_numpy()

        # print(prim_comp)
        # print(compositions_data.loc[compositions_data.index.str.contains('Primary')])

        # breakpoint()

        # alpha_furnace = np.array([0])
        # alpha_furnace = np.full(num_products, 0.5)
        # alpha_furnace = np.full(num_products, 0)
        # alpha_furnace = np.array([0, 0, 0, 0])                  # CHANGE THIS to set weight fraction for furnace constraint FOR EACH PRODUCT


        # Search for index of specific temperature
        # temp_index = np.logical_and(temperature > 583, temperature < 584)                           # CHANGE THIS to set temperature
        # temp_index = 54

        # Determine compositions of separated supplies

        bin1comp = compositions_data.loc[compositions_data.index.str.contains('Kelly LIBS 5xxx')].to_numpy()
        bin2comp = compositions_data.loc[compositions_data.index.str.contains('Kelly LIBS 6xxx')].to_numpy()
        bin3comp = compositions_data.loc[compositions_data.index.str.contains('Kelly LIBS Other')].to_numpy()

        # bin1comp = separation_data[['bin1comp_' + ele.lower() for ele in elements_list]].to_numpy() * 100
        # bin2comp = separation_data[['bin2comp_' + ele.lower() for ele in elements_list]].to_numpy() * 100
        # bin3comp = separation_data[['bin3comp_' + ele.lower() for ele in elements_list]].to_numpy() * 100

        # solcomp = np.array(data.loc[temp_index, 'solcomp_al':'solcomp_fe']).flatten() * 100         # Extracts values for composition of solid phases at given temp
        # bin1comp = separation_data[['bin1comp_al', 'bin1comp_cu', 'bin1comp_mg', 'bin1comp_mn', 'bin1comp_si', 'bin1comp_fe', 'bin1comp_zn']].to_numpy() * 100

        # liqcomp = np.array(data.loc[temp_index, 'liqcomp_al':'liqcomp_fe']).flatten() * 100         # Extracts values for composition of liquid phase at given temp
        # bin2comp = separation_data[['bin2comp_al', 'bin2comp_cu', 'bin2comp_mg', 'bin2comp_mn', 'bin2comp_si', 'bin2comp_fe','bin2comp_zn']].to_numpy() * 100

        # bin3comp = separation_data[['bin3comp_al', 'bin3comp_cu', 'bin3comp_mg', 'bin3comp_mn', 'bin3comp_si', 'bin3comp_fe','bin3comp_zn']].to_numpy() * 100

        fractions = separation_data[year].to_numpy().reshape(1, -1)       # Mass fractions of of each bin for a given year

        # fractions = separation_data[['mass_bin1', 'mass_bin2', 'mass_bin3']].to_numpy()       # Mass fractions of liquid and solid at set separation temperature

        # print("Old scrap comp: ", start_comp)

        if scrapsep_index.size != 0:
            SS_comp = np.hstack((bin1comp,bin2comp,bin3comp)).reshape(-1,num_elements)
            scrap_comp = np.concatenate((start_comp, SS_comp))
        else:
            scrap_comp = start_comp


        # Demand composition lower ranges
        comp_lower = compositions_lower.to_numpy()      

        # Demand composition upper ranges
        comp_upper = compositions_upper.to_numpy()

        # Emissions data
        emissions_frozen = extract_emissions_data(emissions_data_frozen, year)
        emissions_moderate = extract_emissions_data(emissions_data_moderate, year)
        emissions_aggressive = extract_emissions_data(emissions_data_aggressive, year)

        # print(compositions_lower)
        # print(compositions_midrange)
        # print(compositions_upper)

        # print((sum(eol_supply)))

        # print((sum(eol_supply) * sheet_from_all_conversion)/ (collection_rate * scrap_proc_yield))

        # print(start_comp)       # 2D
        # print(scrap_comp)       # 2D
        # print(comp_lower)       
        # print(comp_upper)

        # print(demand)
        # print(supply)
        # print(eol_supply[0])
        # print(sum(eol_supply))

        # print(prim_comp)
        # print(len(prim_comp))

        # print(supply)
        # print(num_scrap)
        # exit()

        # breakpoint()


        # Al, Cu, Mg, Mn, Si, Fe, Zn
        #########################################################################################################################################################################################
        """Set up decision variables for optimization"""
        #########################################################################################################################################################################################

        # Quantity of primary material to be used for each product
        primary = m.addVars(num_products, len(prim_comp), lb=0, vtype=GRB.CONTINUOUS, name="Primary Material")
        # (:,0) = Al; (:,1) = Cu; (:,2) = Mg; (:,3) = Mn; (:,4) = Si; (:,5) = Fe; (:,6) = Zn

        scrap = m.addVars(num_products, scrap_source, lb=0, vtype=GRB.CONTINUOUS, name="Scrap Material")
        # scrap = m.addVars(num_products, scrap_source, lb=, vtype=GRB.CONTINUOUS, name="Scrap Material")
        # (0,0) = scrap source 1 used for product 1; (0,1) = scrap source 2 used for product 1; etc.

        unsep = m.addVars(num_scrap, lb=0, vtype=GRB.CONTINUOUS, name="Scrap Supply TOTAL")
        # total quantity of each unseparated scrap source

        if scrapsep_index.size > 0:
            sep = m.addVars(num_sep//3, 3, lb=0, vtype=GRB.CONTINUOUS, name="Separated Supply TOTAL")
            # total quantity of each separated scrap source

        secondary_virgin = m.addVars(num_products, len(prim_comp), lb=0, vtype=GRB.CONTINUOUS, name="Virgin Material for Secondary Production")

        secondary = m.addVars(num_products, lb=0, vtype=GRB.CONTINUOUS, name="Secondary Material Total")

        # # Binary variable that determines if a temperature is selected or not
        # opt_temp = m.addVars(num_temps, lb=0, vtype=GRB.BINARY, name="Optimal Temperature")

        # # opt_temp_index = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Temperature Index")

        # # opt_fraction = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Fraction Set")

        # opt_solcomp = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Solid Composition")

        # opt_liqcomp = m.addVar(lb=0, vtype=GRB.INTEGER, name="Optimal Liquid Composition")

        # print(range(len(solcomp)))
        # print(range(len(fractions)))
        # print(range(len(separation_data.index)))

        # test = np.array([[1, 2],
        #                  [3, 4]])
        # for i in range(num_scrap, num_sep + num_scrap):
        #     print(i)

        # print(num_scrap)
        

        #########################################################################################################################################################################################
        """Constraints"""
        #########################################################################################################################################################################################

        # Supply constraint
        m.addConstr(scrap.sum() <= np.sum(supply), "Total supply constraint")           # All scrap to be used can't exceed the total amount of scrap available

        # Additional supply constraints for separation
        m.addConstrs((unsep[i] == supply[i] for i in nonsep_index), "Secondary supply constraint (not separated)")                        # For the scrap sources not separated (i.e. manufacturing scrap), the unsep variable is set to supply quantity at relevant indices
        if scrapsep_index.size > 0:
            m.addConstrs((unsep[i] + sep.sum() == supply[i] for i in scrapsep_index), "Secondary supply constraint (separated)")         # Total amount of scrap is balanced for separated scrap stream
            # This constraint won't work if multiple original scrap streams are to be separated

        # Quantity of each scrap used for all prods <= total amount of each scrap
        m.addConstrs((scrap.sum("*",i) <= unsep[i] for i in range(num_scrap)), "Used scrap supply constraint (not separated)")                # Unseparated scrap sources are the first num_scrap scrap sources
        m.addConstrs((scrap.sum("*",i) <= furnace_yield * sep[((i - num_scrap)//3),((i - num_scrap)%3)] for i in range(num_scrap, scrap_source)), "Used scrap supply constraint (separated)")         # Separated scrap sources are the remaining scrap sources
        # m.addConstrs((scrap.sum("*",i) <= sep[((i - num_scrap)//2),((i - num_scrap)%2)] for i in range(num_scrap, scrap_source)), "Used scrap supply constraint (separated)")         # Separated scrap sources are the remaining scrap sources


        # Demand constraint
        m.addConstrs(((primary.sum(i,"*") + secondary[i]) * furnace_yield >= demand[i] for i in range(num_products)), "Demand constraint")
        # m.addConstrs((primary.sum(i,"*") + secondary[i] >= demand[i] for i in range(num_products)), "Demand constraint")
            # 0.95 = 5% melt loss

        # Secondary production of each product is the sum of all scrap in each product and virgin material in each product
        m.addConstrs((scrap.sum(i,"*") + secondary_virgin.sum(i, "*") == secondary[i] for i in range(num_products)), "Secondary production quantity sum")

        # Mass fractions constraint
        # m.addConstr(opt_temp.sum() == 1, "Single temperature choosing constraint")           # Choose only one temp
        # m.addConstr((opt_temp_index == sum(opt_temp[i] * temp_indices[i] for i in range(num_temps))), "Single temperature indexing constraint")

        # opt_fraction = sum(fractions[i] * opt_temp[i] for i in range(num_temps))        # Sum effectively removes zero values

        # m.addConstrs((fractions[i] * (supply[j] - unsep[j]) == sep[j,i] for i in range(num_sep) for j in scrapsep_index), "Mass fractions constraint")
        # print(scrapsep_index.size)
        if scrapsep_index.size > 0:
            m.addConstrs((fractions[j][i] * (supply[k] - unsep[k] - sum(sep.sum(l,"*") for l in range(num_proc) if l != j)) == sep[j,i] for i in range(3) for j in range(num_proc) for k in scrapsep_index), "Mass fractions constraint")

        # m.addConstr(scrap.sum("*", (len(scrap_comp)-1)) == 0, name="No scrap from last source (bin 3)")

        # Lower composition constraint for primary production
        m.addConstrs((sum(prim_comp[l][k] * primary[i,l] for l in range(len(prim_comp))) >= (comp_lower[i][k] * primary.sum(i,"*")) for i in range(num_products) for k in range(num_elements)), "Lower composition constraint for primary material")
            # Concentration (100% element k) * quantity of element k primary used for product i >= lower comp limit of element k used for product i * all primary material used for product i

        # Upper composition constraint for primary production
        m.addConstrs((sum(prim_comp[l][k] * primary[i,l] for l in range(len(prim_comp))) <= (comp_upper[i][k] * primary.sum(i,"*")) for i in range(num_products) for k in range(num_elements)), "Upper composition constraint for primary material")


        # opt_solcomp = sum(solcomp[i] * opt_temp[i] for i in range(num_temps))
        # opt_liqcomp = sum(liqcomp[i] * opt_temp[i] for i in range(num_temps))
        # SS_comp = np.array([opt_solcomp,
        #                     opt_liqcomp])

        # SS_comp = np.hstack((solcomp,liqcomp)).reshape(-1,7)

        # scrap_comp = start_comp

        # scrap_comp = np.concatenate((start_comp, SS_comp))

        # Lower composition constraint for secondary production
        m.addConstrs((sum(prim_comp[l][k] * secondary_virgin[i,l] for l in range(len(prim_comp))) + sum(scrap_comp[j][k] * scrap[i,j] for j in range(scrap_source)) >= (comp_lower[i][k] * secondary[i]) for i in range(num_products) for k in range(num_elements)), "Lower composition constraint for secondary material")
            # 100 * sec_virg[prod1, Al] + (scrap_comp[source1][Al] * scrap[prod1, source1] scrap_comp[source2][Al])


        # Upper composition constraint for secondary production
        m.addConstrs((sum(prim_comp[l][k] * secondary_virgin[i,l] for l in range(len(prim_comp))) + sum(scrap_comp[j][k] * scrap[i,j] for j in range(scrap_source)) <= (comp_upper[i][k] * secondary[i]) for i in range(num_products) for k in range(num_elements)), "Upper composition constraint for secondary material")


        # Furnace constraint for secondary production
        m.addConstrs((alpha_furnace[i] * secondary[i] <= scrap.sum(i,"*") for i in range(num_products)), "Furnace charge constraint")
        # If secondary is under (furnace) percentage of scrap, use for primary instead of secondary


        # # Manually force closed loop reuse of scrap into original source
        # for i in range(0,6):
        #     for j in range(1,8):
        #          if j != i+1:
        #             m.addConstr(scrap[i,j] == 0)

        # m.addConstrs((scrap.sum("*",i) >= 0.3 * sum(scrap.sum("*",j) for j in range(num_scrap, scrap_source)) for i in range(num_scrap, scrap_source)), "Minimum separated scrap used constraint")
        # m.addConstrs((scrap.sum("*",i) >= 0.3 * sum(scrap.sum("*",i)) for i in range(num_scrap, scrap_source)), "Minimum separated scrap used constraint")


        m.ModelSense = GRB.MINIMIZE
        m.params.NonConvex = 2

        # Set objective
        if objective == "primary":
            m.setObjective(primary.sum() + secondary_virgin.sum(), GRB.MINIMIZE)
        # m.setObjective(8510000 * primary.sum("*", 0) + 8510000 * secondary_virgin.sum("*", 0) + 30000 * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + 513000 * secondary.sum(), GRB.MINIMIZE)
        # m.setObjective(prim_al_emissions * primary.sum("*", 0) + prim_cu_emissions * primary.sum("*", 1) + prim_mg_emissions * primary.sum("*", 2) + prim_mn_emissions * primary.sum("*", 3) + prim_si_emissions * primary.sum("*", 4) + prim_fe_emissions * primary.sum("*", 5) + \
        #                prim_al_emissions * secondary_virgin.sum("*", 0) + prim_cu_emissions * secondary_virgin.sum("*", 1) + prim_mg_emissions * secondary_virgin.sum("*", 2) + prim_mn_emissions * secondary_virgin.sum("*", 3) + prim_si_emissions * secondary_virgin.sum("*", 4) + prim_fe_emissions * secondary_virgin.sum("*", 5) + \
        #                scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
        #                sec_al_emissions * secondary.sum() + \
        #                fc_emissions * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))), GRB.MINIMIZE)
        # m.setObjective(prim_al_emissions * primary.sum("*", 0) + prim_al_emissions * secondary_virgin.sum("*", 0) + scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + sec_al_emissions * secondary.sum() + 0 * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))), GRB.MINIMIZE)
        elif objective == "emissions":
            # m.setObjective(prim_al_emissions * primary.sum("*", 0) + prim_cu_emissions * primary.sum("*", 1) + prim_mg_emissions * primary.sum("*", 2) + prim_mn_emissions * primary.sum("*", 3) + prim_si_emissions * primary.sum("*", 4) + prim_fe_emissions * primary.sum("*", 5) + prim_zn_emissions * primary.sum("*", 6) + \
            #                prim_al_emissions * secondary_virgin.sum("*", 0) + prim_cu_emissions * secondary_virgin.sum("*", 1) + prim_mg_emissions * secondary_virgin.sum("*", 2) + prim_mn_emissions * secondary_virgin.sum("*", 3) + prim_si_emissions * secondary_virgin.sum("*", 4) + prim_fe_emissions * secondary_virgin.sum("*", 5) + prim_zn_emissions * secondary_virgin.sum("*", 6) + \
            #                scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
            #                sec_al_emissions * secondary.sum() + \
            #                LIBS_emissions * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))), GRB.MINIMIZE)
            print("emissions optimizing not functional currently")

        # Emissions factors:
            # Primary aluminum: 8.51 kgCO2eq/kg-out = 8.51e6 kgCO2eq/kt-out
            # Scrap preparation (for EOL scrap): 0.03 kgCO2eq/kg-out = 3e4 kgCO2eq/kt-out
            # Secondary aluminum: 0.513 kgCO2eq/kg-out = 5.13e5 kgCO2eq/kt-out

        m.optimize()
        # print (m.display())

        output = {}

        # emissions = m.getObjective().getValue()
        # emissions = (prim_al_emissions * primary.sum("*", 0) + prim_cu_emissions * primary.sum("*", 1) + prim_mg_emissions * primary.sum("*", 2) + prim_mn_emissions * primary.sum("*", 3) + prim_si_emissions * primary.sum("*", 4) + prim_fe_emissions * primary.sum("*", 5) + prim_zn_emissions * primary.sum("*", 6) + \
        #              prim_al_emissions * secondary_virgin.sum("*", 0) + prim_cu_emissions * secondary_virgin.sum("*", 1) + prim_mg_emissions * secondary_virgin.sum("*", 2) + prim_mn_emissions * secondary_virgin.sum("*", 3) + prim_si_emissions * secondary_virgin.sum("*", 4) + prim_fe_emissions * secondary_virgin.sum("*", 5) + prim_zn_emissions * secondary_virgin.sum("*", 6) + \
        #              scrap_prep_emissions * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
        #              sec_al_emissions * secondary.sum() + \
        #              LIBS_emissions * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + \
        #              rolling_emissions * 0.66 * (sum(demand)) + \
        #              blanking_stamping_emissions * 0.66 * 0.95 * 0.70 * (sum(demand)) + \
        #              assembly_emissions * 0.66 * 0.95 * 0.70 * 0.995 * (sum(demand))).getValue() / 1000000   # Rolling, Blanking, Stamping, and Assembly emissions
        # print("Emissions (kgCO2eq/kg-in): %g \n" % emissions)

        emissions_frozen_total = calculate_total_emissions(emissions_frozen, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap)
        emissions_moderate_total = calculate_total_emissions(emissions_moderate, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap)
        emissions_aggressive_total = calculate_total_emissions(emissions_aggressive, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap)

        print("Emissions Frozen (kgCO2eq/kg-in): %g" % emissions_frozen_total)
        print("Emissions Moderate (kgCO2eq/kg-in): %g" % emissions_moderate_total)
        print("Emissions Aggressive (kgCO2eq/kg-in): %g \n" % emissions_aggressive_total)

        # print(m.getObjective())

        for v in m.getVars():
            # print('%s %g' % (v.varName, v.x))
            output[v.varName] = [v.x]
            # print(v.ConstrName)

        Cnames = m.getAttr('constrName', m.getConstrs())

        prim_preML = primary.sum().getValue()
        prim_postML = furnace_yield * primary.sum().getValue()

        # print(primary)

        print("Total amount of primary virgin material used (before melt loss): %g" % prim_preML)
        print("Total amount of primary virgin material used (post melt loss): %g \n" % prim_postML)

        secvirg_preML = secondary_virgin.sum().getValue()
        secvirg_postML = furnace_yield * secondary_virgin.sum().getValue()

        print("Total amount of virgin material in secondary furnace used (before melt loss): %g" % secvirg_preML)
        print("Total amount of virgin material in secondary furnace used (post melt loss): %g \n" % secvirg_postML)

        # eol_scrapsum = (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))).getValue()
        # totaleol = (eol_scrapsources[year].to_numpy())[0]

        eol_scrapsum_RR = ((sum(scrap.sum("*", j) for j in range(eol_scrapsources.shape[0]))) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source)) / furnace_yield).getValue()
        eol_scrapsum_RC = ((sum(scrap.sum("*", j) for j in range(eol_scrapsources.shape[0]))) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source)) / furnace_yield).getValue() * furnace_yield
    
        if 'EOL cap' in folder:
            if eol_scrapsum_RR > ((sum(eol_supply)) * sheet_from_all_conversion):
                eol_scrapsum_RR = ((sum(eol_supply)) * sheet_from_all_conversion)
                eol_scrapsum_RC = eol_scrapsum_RR * furnace_yield
                print("EOL scrap cap applied")

        totaleol = ((sum(eol_supply)) * sheet_from_all_conversion) / (collection_rate * scrap_proc_yield)
    
        print("Total EOL scrap used (before melt loss): %g" % eol_scrapsum_RR)
        print("Total EOL scrap used (post melt loss): %g" % eol_scrapsum_RC)
        print("Total EOL scrap collected (assume 0.95 collection rate): %g" % (totaleol * collection_rate))
        print("Total EOL scrap available: %g \n" % totaleol)

        manuf_scrapsum_RR = ((sum(scrap.sum("*", i) for i in range(eol_scrapsources.shape[0], num_scrap)))).getValue()
        manuf_scrapsum_RC = ((sum(scrap.sum("*", i) for i in range(eol_scrapsources.shape[0], num_scrap)))).getValue() * furnace_yield
        totalmanuf = sum(manuf_supply)

        print("Total manufacturing scrap used (before melt loss): %g" % manuf_scrapsum_RR)
        print("Total manufacturing scrap used (post melt loss): %g" % manuf_scrapsum_RC)
        print("Total manufacturing scrap available: %g \n" % totalmanuf)
       
        # print(output)
        scrapsum_RR = eol_scrapsum_RR + manuf_scrapsum_RR
        scrapsum_RC = eol_scrapsum_RC + manuf_scrapsum_RC
        # totaleol = (supply_data.loc[~supply_data['Sector'].str.startswith('All')])[year].to_numpy()
        # totaleol = (sum(eol_supply) * sheet_from_all_conversion)/ (collection_rate * scrap_proc_yield)
        totalsupply = totaleol + totalmanuf
        print("Total amount of scrap used (before melt loss): %g" % scrapsum_RR)
        print("Total amount of scrap used (post melt loss): %g" % scrapsum_RC)
        print("Total amount of scrap available: %g \n" % totalsupply)


        totaldemand = sum(demand)

        # print("Total demand: %g" % totaldemand)

        RR = scrapsum_RR/totalsupply
        RC = scrapsum_RC/totaldemand

        RR_EOL = eol_scrapsum_RR/totaleol
        RC_EOL = eol_scrapsum_RC/totaldemand

        RR_manuf = manuf_scrapsum_RR/totalmanuf
        RC_manuf = manuf_scrapsum_RC/totaldemand

        print("Total demand: ", totaldemand, "\n")

        print("RR: ", RR)
        print("RC: ", RC)
        print("EOL RR: ", RR_EOL)
        print("EOL RC: ", RC_EOL)
        print("Manufacturing RR: ", RR_manuf)
        print("Manufacturing RC: ", RC_manuf)

        # print("Separated total: ", sep.sum().getValue())

        # print(demand)

        # print("Optimal separation temperature: %g" % temperature[opt_temp_index.x])

        # # print(num_sep)
        # # print(num_scrap)
        # # print("Test here %g" % (range(num_scrap)))
        # for i in range(scrap_source):
        #     print("scrap sum: %g" % (scrap.sum("*",i)).getValue())
        # # for i in scrapsep_index:
        # #     print(i)

        # # print(primary.sum(0,"*"))

        # # print(SS_comp)

        # print(unsep)
        ranked_constraints_df = rank_filtered_constraints_by_objective_impact(m, demand_list, elements_list)

        # Construct column labels
        primary_virgin_labels = ['Primary virgin ' + ele for ele in elements_list]
        secondary_virgin_labels = ['Secondary virgin ' + ele for ele in elements_list]
        eol_scrap_labels = [eolscrap for eolscrap in scrap_list]
        manufacturing_scrap_labels = [dem + ' manufacturing scrap' for dem in demand_list]
        if to_mix.size != 0:
            mixed_manufacturing_labels = [f'Mixed {to_mix} manufacturing scrap']
        else:
            mixed_manufacturing_labels = []
        if scrapsep_index.size > 0:
            separated_stream_labels = [f'Separated stream {i//3, i%3}' for i in range(num_sep)]
        else:
            separated_stream_labels = []

        columns = (primary_virgin_labels + secondary_virgin_labels + eol_scrap_labels +
                   manufacturing_scrap_labels + mixed_manufacturing_labels + separated_stream_labels)

        # print("Final Columns Labels:", columns)

        # Initialize DataFrame to hold the results
        num_rows = len(demand_list)
        num_cols = len(columns)
        table = pd.DataFrame(np.zeros((num_rows, num_cols)), index=demand_list, columns=columns)

        # Fill in the table with results from the dictionary
        for key, value in output.items():
            # Check if 'value' is a list and has exactly one element
            if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (int, float)):
                value = value[0]  # Extract the single float from the list
            
            if isinstance(value, (int, float)):  # Ensure extracted value is a number
                if 'Primary Material' in key:
                    i, j = map(int, key[len('Primary Material') + 1 : -1].split(','))
                    table.iat[i, j] = value
                elif 'Virgin Material for Secondary Production' in key:
                    i, j = map(int, key[len('Virgin Material for Secondary Production') + 1 : -1].split(','))
                    j += len(elements_list)
                    table.iat[i, j] = value
                elif 'Scrap Material' in key:
                    i, j = map(int, key[len('Scrap Material') + 1 : -1].split(','))
                    j += 2 * len(elements_list)
                    table.iat[i, j] = value
            else:
                print(f"Skipping key '{key}' with unexpected value type or structure: {value}")

        # Ensure indices are valid
        # print("Table after processing:", table)

        # Calculate row sums and append as a new column
        row_sums = table.sum(axis=1)
        table['Row Total'] = row_sums

        # Calculate 'Total Demand (adjusted for melt yield)'
        table['Total Demand (adjusted for melt yield)'] = row_sums * furnace_yield

        # Calculate and append column sums (including new columns)
        col_sums = table.sum(axis=0)

        # Create a new row for column totals, with NaN for 'Row Total' and 'Total Demand'
        sum_row = pd.Series(np.append(col_sums.values[:-2], [np.nan, np.nan]), index=table.columns)
        table = pd.concat([table, sum_row.to_frame().T], ignore_index=True)
        table.index = list(demand_list) + ['Column Total']

        # Display the final table
        # print("Final Table with Total Demand Adjustment:")
        # print(table)

        # DMFA INPUTS sheet
        # Create DataFrame for "Demand alloys"
        demand_alloys_df = pd.DataFrame({
            'Demand alloys': demand_list,
            str(year): demand
        })

        # Combine all scrap labels
        scrap_sources_labels = eol_scrap_labels + manufacturing_scrap_labels + mixed_manufacturing_labels
        
        # Adjust only the eol_scrap part of supply by dividing by 0.95
        adjusted_supply = supply.copy()  # Create a copy to preserve original data
        adjusted_supply[:len(eol_scrap_labels)] /= (collection_rate * scrap_proc_yield)  # Only adjust the eol_scrap part

        # Create DataFrame for "Scrap sources"
        scrap_sources_df = pd.DataFrame({
            'Scrap sources': scrap_sources_labels,
            str(year): adjusted_supply
        })

        # GUROBI OUTPUTS RAW sheet
        gurobi_output_df = pd.DataFrame(output)

        # INPUT SCRAP COMPS sheet
        scrap_comps_df = pd.DataFrame(start_comp, index=scrap_sources_labels, columns=elements_list)

        if snapshot == True:
            filepath = folder + str(year) + '_domesticABS_' + label + '.xlsx'

            # Set up ExcelWriter
            with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
                
                # OUTPUTS sheet
                table.to_excel(writer, sheet_name='OUTPUTS')

                # RANKED CONSTRAINTS sheet
                ranked_constraints_df.to_excel(writer, sheet_name='RANKED CONSTRAINTS', index=False)

                # DMFA INPUTS sheet
                demand_alloys_df.to_excel(writer, sheet_name='DMFA INPUTS', index=False, startrow=0)
                scrap_sources_df.to_excel(writer, sheet_name='DMFA INPUTS', index=False, startrow=len(demand_list) + 3)

                # INPUT SCRAP COMPS sheet
                scrap_comps_df.to_excel(writer, sheet_name='INPUT SCRAP COMPS')

                # GUROBI OUTPUTS RAW sheet
                gurobi_output_df.to_excel(writer, sheet_name='GUROBI OUTPUTS RAW', index=False)

            print("Excel file created successfully with four sheets.")

        # ## save to xlsx file

        # filepath = r'/Users/alissatsai/Documents/Fall 2024 University of Michigan/Aluminum optimization/Domestic ABS Results/Kelly LIBS/2035_domesticABS_KellyLIBS_constraints.xlsx'

        # df.to_excel(filepath, index=False)


        # # Convert quantity data into dataframe
        # df = pd.DataFrame(output)

        # ## save to xlsx file

        # filepath = r'/Users/alissatsai/Documents/Fall 2024 University of Michigan/Aluminum optimization/Domestic ABS Results (min prim)/Kelly LIBS/2035_domesticABS_KellyLIBS_raw.xlsx'

        # df.to_excel(filepath, index=False)


        return(prim_preML, secvirg_preML, eol_scrapsum_RR, manuf_scrapsum_RR, RR, RC, RR_EOL, RC_EOL, RR_manuf, RC_manuf, emissions_frozen_total, emissions_moderate_total, emissions_aggressive_total)

    except gp.GurobiError as e:
        print('Error code ' + str(e.errno) + ': ' + str(e))

    except AttributeError:
        print('Encountered an attribute error')

def extract_emissions_data(emissions_data, year):
    """Extract all emissions factors for a given year from emissions data"""
    return {
        'prim_al': emissions_data.loc[emissions_data.index.str.startswith('Primary Al')][year].to_numpy()[0],
        'prim_cu': emissions_data.loc[emissions_data.index.str.startswith('Primary Cu')][year].to_numpy()[0],
        'prim_mg': emissions_data.loc[emissions_data.index.str.startswith('Primary Mg')][year].to_numpy()[0],
        'prim_mn': emissions_data.loc[emissions_data.index.str.startswith('Primary Mn')][year].to_numpy()[0],
        'prim_si': emissions_data.loc[emissions_data.index.str.startswith('Primary Si')][year].to_numpy()[0],
        'prim_fe': emissions_data.loc[emissions_data.index.str.startswith('Primary Fe')][year].to_numpy()[0],
        'prim_zn': emissions_data.loc[emissions_data.index.str.startswith('Primary Zn')][year].to_numpy()[0],
        'scrap_prep': emissions_data.loc[emissions_data.index.str.startswith('Scrap')][year].to_numpy()[0],
        'sec_al': emissions_data.loc[emissions_data.index.str.startswith('Secondary')][year].to_numpy()[0],
        'fc': emissions_data.loc[emissions_data.index.str.startswith('Fractional')][year].to_numpy()[0],
        'LIBS': emissions_data.loc[emissions_data.index.str.startswith('LIBS')][year].to_numpy()[0],
        'rolling': emissions_data.loc[emissions_data.index.str.startswith('Sheet rolling')][year].to_numpy()[0],
        'blanking_stamping': emissions_data.loc[emissions_data.index.str.startswith('Sheet blanking')][year].to_numpy()[0],
        'assembly': emissions_data.loc[emissions_data.index.str.startswith('Vehicle assembly')][year].to_numpy()[0]
    }

def calculate_total_emissions(emissions_factors, primary, secondary_virgin, scrap, secondary, demand, scrap_source, num_scrap):
    """Calculate total emissions given emissions factors and optimization variables"""
    return (emissions_factors['prim_al'] * primary.sum("*", 0) + 
            emissions_factors['prim_cu'] * primary.sum("*", 1) + 
            emissions_factors['prim_mg'] * primary.sum("*", 2) + 
            emissions_factors['prim_mn'] * primary.sum("*", 3) + 
            emissions_factors['prim_si'] * primary.sum("*", 4) + 
            emissions_factors['prim_fe'] * primary.sum("*", 5) + 
            emissions_factors['prim_zn'] * primary.sum("*", 6) + 
            emissions_factors['prim_al'] * secondary_virgin.sum("*", 0) + 
            emissions_factors['prim_cu'] * secondary_virgin.sum("*", 1) + 
            emissions_factors['prim_mg'] * secondary_virgin.sum("*", 2) + 
            emissions_factors['prim_mn'] * secondary_virgin.sum("*", 3) + 
            emissions_factors['prim_si'] * secondary_virgin.sum("*", 4) + 
            emissions_factors['prim_fe'] * secondary_virgin.sum("*", 5) + 
            emissions_factors['prim_zn'] * secondary_virgin.sum("*", 6) + 
            emissions_factors['scrap_prep'] * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + 
            emissions_factors['sec_al'] * secondary.sum() + 
            emissions_factors.get('LIBS', emissions_factors['fc']) * (sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))) + 
            emissions_factors['rolling'] * 0.66 * (sum(demand)) + 
            emissions_factors['blanking_stamping'] * 0.66 * 0.95 * 0.70 * (sum(demand)) + 
            emissions_factors['assembly'] * 0.66 * 0.95 * 0.70 * 0.995 * (sum(demand))).getValue() / 1000000

def rank_filtered_constraints_by_objective_impact(model, demand_list, elements_list):
    ranked_constraints = []
    keyword = "composition"

    for c in model.getConstrs():
        name = c.ConstrName
        if keyword.lower() not in name.lower():
            continue  # Skip if name doesn't contain the keyword

        if abs(c.Slack) > 1e-6 or abs(c.Pi) < 1e-2:
            continue  # Skip if not binding or not impactful

        rhs = c.getAttr("RHS")
        slack = c.Slack
        pi = c.Pi                   # Shadow price
        sarhs_low = c.SARHSLow
        sarhs_up = c.SARHSUp

        allow_dec = rhs - sarhs_low
        allow_inc = sarhs_up - rhs

        # Estimated impact on objective
        impact_dec = abs(pi * allow_dec)
        impact_inc = abs(pi * allow_inc)
        max_impact = max(impact_dec, impact_inc)

        # print (f"Constraint: {name}, Slack: {slack}, Shadow Price: {pi}, Allowable decrease: {allow_dec}, Allowable increase: {allow_inc}")

        # Decode the name by replacing the indices with actual values
        if "[" in name and "]" in name:
            try:
                indices = name[name.index("[") + 1 : name.index("]")].split(",")
                a, b = int(indices[0]), int(indices[1])
                # decoded_name = f"{name[:name.index('[')]}[{demand_list[a]}, {elements_list[b]}]"
                if "Lower" in name:
                    low_or_up = "Lower"
                elif "Upper" in name:
                    low_or_up = "Upper"
                if "secondary" in name:
                    prim_or_sec = "secondary"
                elif "primary" in name:
                    prim_or_sec = "primary"
                decoded_name = f"{low_or_up} bound on {elements_list[b]} in {demand_list[a]}({prim_or_sec} production)"
            except (ValueError, IndexError):
                decoded_name = name  # If decoding fails, keep the original name
        else:
            decoded_name = name

        ranked_constraints.append((decoded_name, max_impact, slack, pi, allow_dec, allow_inc))

    # Sort by descending impact
    ranked_constraints.sort(key=lambda x: x[1], reverse=True)

    # Convert to a DataFrame
    ranked_constraints_df = pd.DataFrame(
        ranked_constraints,
        columns=["Name", "Max est. impact", "Slack", "Shadow Price", "Allowable decrease", "Allowable increase"]
    )

    # Display results
    for _, row in ranked_constraints_df.iterrows():
        # print(f"{row['Name']}: Max est. impact = {row['Max est. impact']:.4f}, Shadow Price = {row['Shadow Price']:.4f}, Allowable decrease = {row['Allowable decrease']:.4f}, Allowable increase = {row['Allowable increase']:.4f}")
        print(f"{row['Name']}: Max est. impact = {row['Max est. impact']}, Slack = {row['Slack']}, Shadow Price = {row['Shadow Price']}, Allowable decrease = {row['Allowable decrease']}, Allowable increase = {row['Allowable increase']}")

    return ranked_constraints_df

if __name__ == '__main__':
    # eol_supply = 121
    # Ford_lowCu = 104
    # Ford_highCu = 116
    # Ford_lowMg = 38
    # Ford_highMg = 24

    year = 2050
    scenario = "LIBS + HRC"
    folder = r'/Users/alissatsai/Documents/Winter 2025 University of Michigan/Aluminum optimization/Domestic ABS Results EOL cap TEST (min prim)/' + scenario + '/'


    if "LIBS" in scenario and year > 2025:
        export = optimize_ABS_LIBS("primary", year, scenario, False, folder)
    else:
        export = optimize_ABS("primary", year, scenario, False, folder)

    print(export)