# Optimize quantity of domestic aluminum scrap used in aluminum alloy sheet products

import numpy as np
import gurobipy as gp
from gurobipy import GRB
import pandas as pd
import json
import sys
import re
import itertools
import os

def optimize(objective, year, scenario, emissions_scenario='moderate'):
    """Optimize aluminum scrap usage
    
    Args:
        objective: 'primary' to minimize primary material, 'emissions' to minimize emissions
        year: Year for analysis
        scenario: Scenario name
        emissions_scenario: 'frozen', 'moderate', or 'aggressive' (only used when objective='emissions')

    Returns:
        Gurobi model object after optimization
    """
    try:
        # Create a new model
        m = gp.Model("Al-opt")

        # Generate values for input variables into model
        inputs = input_values(year, scenario)
        num_products = inputs["num_products"]
        num_elements = inputs["num_elements"]
        scrap_source = inputs["scrap_source"]
        num_scrap = inputs["num_scrap"]
        scrapsep_index = inputs["scrapsep_index"]
        num_sep = inputs["num_sep"]
        supply = inputs["supply"]
        nonsep_index = inputs["nonsep_index"]
        furnace_yield = inputs["furnace_yield"]
        demand = inputs["demand"]
        fractions = inputs["fractions"]
        num_temps = inputs["num_temps"]
        prim_comp = inputs["prim_comp"]
        comp_lower = inputs["comp_lower"]
        comp_upper = inputs["comp_upper"]
        scrap_comp = inputs["scrap_comp"]
        alpha_furnace = inputs["alpha_furnace"]

        #########################################################################################################################################################################################
        """Set up decision variables for optimization"""
        #########################################################################################################################################################################################

        # Quantity of primary material to be used for each product
        primary = m.addVars(num_products, len(prim_comp), lb=0, vtype=GRB.CONTINUOUS, name="Primary Material")
        # (:,0) = Si; (:,1) = Fe; (:,2) = Cu; (:,3) = Mn; (:,4) = Mg; (:,5) = Cr; (:,6) = Ni; (:,7) = Zn; (:,8) = Ti

        scrap = m.addVars(num_products, scrap_source, lb=0, vtype=GRB.CONTINUOUS, name="Scrap Material")
        # (0,0) = scrap source 1 used for product 1; (0,1) = scrap source 2 used for product 1; etc.

        unsep = m.addVars(num_scrap, lb=0, vtype=GRB.CONTINUOUS, name="Scrap Supply TOTAL")
        # total quantity of each unseparated scrap source

        if scrapsep_index.size > 0:
            sep = m.addVars(num_sep//2, 2, lb=0, vtype=GRB.CONTINUOUS, name="Separated Supply TOTAL")
            # total quantity of each separated scrap source

        secondary_virgin = m.addVars(num_products, len(prim_comp), lb=0, vtype=GRB.CONTINUOUS, name="Virgin Material for Secondary Production")

        secondary = m.addVars(num_products, lb=0, vtype=GRB.CONTINUOUS, name="Secondary Material Total")

        #########################################################################################################################################################################################
        """Constraints"""
        #########################################################################################################################################################################################

        # Supply constraint
        # Ensures scrap to be used can't exceed the total amount of scrap available
        m.addConstr(scrap.sum() <= np.sum(supply), "Total supply constraint")

        # Additional supply constraints for separation
        m.addConstrs((unsep[i] == supply[i] for i in nonsep_index), "Secondary supply constraint (not separated)")                        # For the scrap sources not separated (i.e. manufacturing scrap), the unsep variable is set to supply quantity at relevant indices
        if scrapsep_index.size > 0:
            m.addConstrs((unsep[i] + sep.sum() == supply[i] for i in scrapsep_index), "Secondary supply constraint (separated)")         # Total amount of scrap is balanced for separated scrap stream
            # This constraint works for separating one scrap stream only

        # Quantity of each scrap used for all prods <= total amount of each scrap
        m.addConstrs((scrap.sum("*",i) <= unsep[i] for i in range(num_scrap)), "Used scrap supply constraint (not separated)")                # Unseparated scrap sources are the first num_scrap scrap sources
        m.addConstrs((scrap.sum("*",i) <= furnace_yield * sep[((i - num_scrap)//2),((i - num_scrap)%2)] for i in range(num_scrap, scrap_source)), "Used scrap supply constraint (separated)")         # Separated scrap sources are the remaining scrap sources

        # Demand constraint
        m.addConstrs(((primary.sum(i,"*") + secondary[i]) * furnace_yield >= demand[i] for i in range(num_products)), "Demand constraint")

        # Secondary production of each product is the sum of all scrap in each product and virgin material in each product
        m.addConstrs((scrap.sum(i,"*") + secondary_virgin.sum(i, "*") == secondary[i] for i in range(num_products)), "Secondary production quantity sum")

        # Constraint to prevent use of primary materials for unconstrained elements
        m.addConstrs(
            (
                primary[i,l] == 0
                for i in range(num_products) for l in range(len(prim_comp))
                for k in range(num_elements)
                if prim_comp[l][k] > 0.5 and np.isnan(comp_lower[i][k]) and np.isnan(comp_upper[i][k])
            ),
            "Prevent primary materials for unconstrained elements"
        )

        # Lower composition constraint for primary production
        m.addConstrs(
            (
                sum(prim_comp[l][k] * primary[i,l] for l in range(len(prim_comp))) >= (comp_lower[i][k] * primary.sum(i,"*")) 
                for i in range(num_products) for k in range(num_elements) 
                if not np.isnan(comp_lower[i][k])
            ), 
            "Lower composition constraint for primary material"
        )
            # Concentration (100% element k) * quantity of element k primary used for product i >= lower comp limit of element k used for product i * all primary material used for product i

        # Upper composition constraint for primary production
        m.addConstrs(
            (
                sum(prim_comp[l][k] * primary[i,l] for l in range(len(prim_comp))) <= (comp_upper[i][k] * primary.sum(i,"*")) 
                for i in range(num_products) for k in range(num_elements) 
                if not np.isnan(comp_upper[i][k])
            ), 
            "Upper composition constraint for primary material"
        )

        # Constraint to prevent use of secondary virgin materials for unconstrained elements
        m.addConstrs(
            (
                secondary_virgin[i,l] == 0
                for i in range(num_products) for l in range(len(prim_comp))
                for k in range(num_elements)
                if prim_comp[l][k] > 0.5 and np.isnan(comp_lower[i][k]) and np.isnan(comp_upper[i][k])
            ),
            "Prevent secondary virgin materials for unconstrained elements"
        )
            # 100 * sec_virg[prod1, Al] + (scrap_comp[source1][Al] * scrap[prod1, source1] scrap_comp[source2][Al])


        # Lower composition constraint for secondary production
        m.addConstrs(
            (
                sum(prim_comp[l][k] * secondary_virgin[i,l] for l in range(len(prim_comp))) + sum(scrap_comp[j][k] * scrap[i,j] for j in range(scrap_source)) >= (comp_lower[i][k] * secondary[i]) 
                for i in range(num_products) for k in range(num_elements)
                if not np.isnan(comp_lower[i][k])
            ), 
            "Lower composition constraint for secondary material"
        )

        # Upper composition constraint for secondary production
        m.addConstrs(
            (
                sum(prim_comp[l][k] * secondary_virgin[i,l] for l in range(len(prim_comp))) + sum(scrap_comp[j][k] * scrap[i,j] for j in range(scrap_source)) <= (comp_upper[i][k] * secondary[i]) 
                for i in range(num_products) for k in range(num_elements)
                if not np.isnan(comp_upper[i][k])
            ), 
            "Upper composition constraint for secondary material"
        )

        # Furnace constraint for secondary production
        m.addConstrs((alpha_furnace[i] * secondary[i] <= scrap.sum(i,"*") for i in range(num_products)), "Furnace charge constraint")

        m.ModelSense = GRB.MINIMIZE
        m.params.NonConvex = 2

        # Set objective
        if objective == "primary":
            m.setObjective(primary.sum() + secondary_virgin.sum(), GRB.MINIMIZE)
        elif objective == "emissions":
            # Select which emissions dataset to use based on emissions_scenario parameter
            if emissions_scenario == 'frozen':
                emissions_data_selected = inputs["emissions_data_frozen"]
            elif emissions_scenario == 'aggressive':
                emissions_data_selected = inputs["emissions_data_aggressive"]
            else:  # default to 'moderate'
                emissions_data_selected = inputs["emissions_data_moderate"]
            
            # Use flexible emissions calculation
            model_vars = {
                'primary': primary,
                'secondary_virgin': secondary_virgin,
                'scrap': scrap,
                'secondary': secondary,
                'num_scrap': num_scrap,
                'scrap_source': scrap_source,
                'prim_comp': prim_comp,
                'prim_comp_df': inputs.get("prim_comp_df", None)
            }
            emissions_objective = calculate_emissions(
                emissions_data_selected, 
                model_vars=model_vars, 
                year=year
            )
            m.setObjective(emissions_objective, GRB.MINIMIZE)
        
        m.optimize()

        # For troubleshooting: print model status, variable values, and objective value
        # print (m.display())

        return m
    
    except gp.GurobiError as e:
        print('Error code ' + str(e.errno) + ': ' + str(e))

    except AttributeError:
        print('Encountered an attribute error')

def load_data(year, scenario):
    """Function that loads data from Excel files

    Args:
        year: Year for analysis
        scenario: Scenario name

    Returns:
        dict: Dictionary containing all relevant dataframes for the specified scenario and year
    """
    # Load key file
    scenarios_references = pd.read_excel('domesticABS_scenarios.xlsx', index_col=[0], sheet_name="Table")
    
    # Define rows for baseline and alternative scenarios
    scenario_row = scenarios_references.loc[[scenario]]
    label = scenario_row["label"].values[0]

    # Converge results at year 2025 except Greene (results up until 2025 are the same for all scenarios except Greene)
    if year <= 2025 and scenario != "Greene":
        scenario_row = scenarios_references.loc[["Baseline"]]

    # Load separation data
    separation_data_file = scenario_row["separation_data_file"].values[0]                   # Finds name of separation data file
    if pd.isna(separation_data_file):
        separation_data = pd.DataFrame()                                                     # If no separation data file is specified, use an empty DataFrame
    else:
        separation_data = pd.read_excel(separation_data_file)                                   # Stores separation data into dataframe
    

    # Load demand data
    demand_dict_file = scenario_row["demand_dict_file"].values[0]                           # Finds name of demand data file
    demand_dict = pd.read_excel(demand_dict_file, sheet_name=None)                          # Stores demand data into dictionary of dataframes
    demand_data = pd.concat(demand_dict.values(), ignore_index=True)                        # Concatenates all dataframes in the dictionary into a single dataframe
    demand_data.set_index("Alloy", inplace=True, drop=True)                                  # Sets index of dataframe to "Alloy" column

    # Load forming scrap data
    forming_scrap_dict_file = scenario_row["forming_scrap_dict_file"].values[0]                             # Finds name of forming scrap data file
    forming_scrap_dict = pd.read_excel(forming_scrap_dict_file, sheet_name=None)                            # Stores forming scrap data into dictionary of dataframes
    forming_scrap_by_alloy_data = pd.concat(forming_scrap_dict.values(), ignore_index=True)                 # Concatenates all dataframes in the dictionary into a single dataframe
    forming_scrap_by_alloy_data.set_index("Alloy", inplace=True, drop=True)                                 # Sets index of dataframe to "Alloy" column

    # Load fabrication scrap data
    fabrication_scrap_dict_file = scenario_row["fabrication_scrap_dict_file"].values[0]                    # Finds name of fabrication scrap data file
    fabrication_scrap_dict = pd.read_excel(fabrication_scrap_dict_file, sheet_name=None)                   # Stores fabrication scrap data into dictionary of dataframes
    fabrication_scrap_by_alloy_data = pd.concat(fabrication_scrap_dict.values(), ignore_index=True)        # Concatenates all dataframes in the dictionary into a single dataframe
    fabrication_scrap_by_alloy_data.set_index("Alloy", inplace=True, drop=True)                            # Sets index of dataframe to "Alloy" column

    # Load composition data
    compositions_dict_file = scenario_row["compositions_dict_file"].values[0]               # Finds name of composition data file
    compositions_dict = pd.read_excel(compositions_dict_file, sheet_name=None)              # Stores composition data into dictionary of dataframes
    compositions_data = pd.concat(compositions_dict.values(), ignore_index=True)            # Concatenates all dataframes in the dictionary into a single dataframe
    compositions_data.set_index("Alloy", inplace=True, drop=True)                           # Sets index of dataframe to "Alloy" column

    # Load supply data
    supply_dict_file = scenario_row["supply_dict_file"].values[0]                           # Finds name of supply data file
    suppy_dict = pd.read_excel(supply_dict_file, sheet_name=None)                           # Stores supply data into dictionary of dataframes
    supply_data = pd.concat(suppy_dict.values(), ignore_index=True)                         # Concatenates all dataframes in the dictionary into a single dataframe
    supply_data.set_index("Alloy", inplace=True, drop=True)                                 # Sets index of dataframe to "Alloy" column

    # Load collection rates
    collection_rates_file = scenario_row["collection_rates_data_file"].values[0]            # Finds name of collection rates data file
    collection_rates_dict = pd.read_excel(collection_rates_file, sheet_name=None)           # Stores collection rates data into dictionary of dataframes
    collection_rates_data = pd.concat(collection_rates_dict.values(), ignore_index=True)    # Concatenates all dataframes in the dictionary into a single dataframe
    collection_rates_data.set_index("Alloy", inplace=True, drop=True)                       # Sets index of dataframe to "Alloy" column

    # Load emissions data - now three scenarios: frozen, moderate, aggressive
    emissions_data_file_frozen = scenario_row["emissions_data_file_frozen"].values[0]       # Finds name of frozen emissions data file
    emissions_data_frozen = pd.read_excel(emissions_data_file_frozen)                       # Stores frozen emissions data into dataframe
    emissions_data_frozen.set_index("Process", inplace=True, drop=True)                     # Sets index of dataframe to "Process" column
    
    emissions_data_file_moderate = scenario_row["emissions_data_file_moderate"].values[0]   # Finds name of moderate emissions data file
    emissions_data_moderate = pd.read_excel(emissions_data_file_moderate)                   # Stores moderate emissions data into dataframe
    emissions_data_moderate.set_index("Process", inplace=True, drop=True)                   # Sets index of dataframe to "Process" column
    
    emissions_data_file_aggressive = scenario_row["emissions_data_file_aggressive"].values[0]       # Finds name of aggressive emissions data file
    emissions_data_aggressive = pd.read_excel(emissions_data_file_aggressive)                       # Stores aggressive emissions data into dataframe
    emissions_data_aggressive.set_index("Process", inplace=True, drop=True)                         # Sets index of dataframe to "Process" column

    # Load product parameters data
    product_parameters_dict_file = scenario_row["product_parameters_dict_file"].values[0]           # Finds name of product parameters data file
    product_parameters_dict = pd.read_excel(product_parameters_dict_file, sheet_name=None)          # Stores product parameters data into dictionary of dataframes
    product_parameters = pd.concat(product_parameters_dict.values(), ignore_index=True)             # Concatenates all dataframes in the dictionary into a single dataframe
    product_parameters.set_index("Alloy", inplace=True, drop=True)                                  # Sets index of dataframe to "Alloy" column

    loaded_data = {
        "scenario_row": scenario_row,
        "separation_data": separation_data,
        "demand_data": demand_data,
        "forming_scrap_by_alloy_data": forming_scrap_by_alloy_data,
        "fabrication_scrap_by_alloy_data": fabrication_scrap_by_alloy_data,
        "compositions_data": compositions_data,
        "supply_data": supply_data,
        "collection_rates_data": collection_rates_data,
        "emissions_data_frozen": emissions_data_frozen,
        "emissions_data_moderate": emissions_data_moderate,
        "emissions_data_aggressive": emissions_data_aggressive,
        "product_parameters": product_parameters,
        "label": label
    }

    return loaded_data

def input_values(year, scenario):
    """Function that returns input values for the optimization

    Args:
        year: Year for analysis
        scenario: Scenario name 

    Returns:
        dict: Dictionary containing all input values for the optimization   
    """

    # Runs loaded_data function to extract all relevant dataframes for the specified scenario and year
    loaded_data = load_data(year, scenario)
    scenario_row = loaded_data["scenario_row"]
    separation_data = loaded_data["separation_data"]
    demand_data = loaded_data["demand_data"]
    forming_scrap_by_alloy_data = loaded_data["forming_scrap_by_alloy_data"]
    fabrication_scrap_by_alloy_data = loaded_data["fabrication_scrap_by_alloy_data"]
    compositions_data = loaded_data["compositions_data"]
    supply_data = loaded_data["supply_data"]
    collection_rates_data = loaded_data["collection_rates_data"]
    emissions_data_frozen = loaded_data["emissions_data_frozen"]
    emissions_data_moderate = loaded_data["emissions_data_moderate"]
    emissions_data_aggressive = loaded_data["emissions_data_aggressive"]
    product_parameters = loaded_data["product_parameters"]

    # Specify elements to remove from composition data (full list of default alloying elements: Si, Fe, Cu, Mn, Mg, Cr, Ni, Zn, Ti)
    compositions_data = compositions_data.drop(columns=["Cr", "Ni", "Ti"])          # Drop Cr, Ni, and Ti, keeping only Si, Fe, Cu, Mn, Mg, and Zn

    # Filter demand_data
    # demand_data = demand_data[demand_data.index.str.contains("Ford")]             # Toggle if system boundary is Ford only (will filter demand data)
    demand_data = demand_data[demand_data.index.str.contains("Auto")]               # Toggle if system boundary is Auto only (will filter demand data)

    # Filter forming_scrap_by_alloy
    # forming_scrap_by_alloy_data = forming_scrap_by_alloy_data[forming_scrap_by_alloy_data.index.str.contains("Ford")]                 # Toggle if system boundary is Ford only (will filter forming scrap data)
    forming_scrap_by_alloy_data = forming_scrap_by_alloy_data[forming_scrap_by_alloy_data.index.str.contains("Auto")]                   # Toggle if system boundary is Auto only (will filter forming scrap data)

    # Filter fabrication_scrap_by_alloy
    # fabrication_scrap_by_alloy_data = fabrication_scrap_by_alloy_data[fabrication_scrap_by_alloy_data.index.str.contains("Ford")]            # Toggle if system boundary is Ford only (will filter fabrication scrap data)
    fabrication_scrap_by_alloy_data = fabrication_scrap_by_alloy_data[fabrication_scrap_by_alloy_data.index.str.contains("Auto")]              # Toggle if system boundary is Auto only (will filter fabrication scrap data)

    # Filter supply_data
    # supply_data = supply_data[supply_data.index.str.contains("Ford")]             # Toggle if system boundary is Ford only (will filter EOL scrap supply data)
    supply_data = supply_data[supply_data.index.str.contains("Auto")]               # Toggle if system boundary is Auto only (will filter EOL scrap supply data)


    # Set number of temperatures (based on separation data file)
    num_temps = len(separation_data.index)                                          # Only relevant for fractional crystallization

    # Extract ingot demand data for the specified year and convert to numpy array
    demand = demand_data[year]

    # Set product list from demand data
    product_list = demand.index.to_numpy()

    # Extract forming and fabrication scrap data for the specified year
    forming_scrap_by_alloy = forming_scrap_by_alloy_data[year]
    fabrication_scrap_by_alloy = fabrication_scrap_by_alloy_data[year]

    # Set dataframe of all EOL scrap - filter by year
    all_eolscrap_generated = supply_data[year]

    # Set collection rates and apply to EOL scrap
    collection_rates = collection_rates_data
    # Applies collection rates and scrap processing yields to EOL scrap and saves in a new dataframe
    all_eolscrap = all_eolscrap_generated.copy()
    
    # Apply collection rates for each alloy individually
    for alloy in all_eolscrap_generated.index:
        if alloy in collection_rates.index:
            # Get the collection rate for this specific alloy and year
            collection_rate_year = float(collection_rates.loc[alloy, year])
            scrap_proc_yield = scenario_row["scrap_proc_yield"].values[0]
            
            # Multiply the EOL scrap for this alloy by its collection rate and scrap processing yield
            all_eolscrap.loc[alloy] = all_eolscrap_generated.loc[alloy] * collection_rate_year * scrap_proc_yield
        else:
            # If no collection rate is defined for this alloy, use the original quantity (effectively 100% collection rate)
            scrap_proc_yield = scenario_row["scrap_proc_yield"].values[0]
            all_eolscrap.loc[alloy] = all_eolscrap_generated.loc[alloy] * scrap_proc_yield

    # Get list of ALL scrap alloys from supply data (for EOL) and scrap data (for forming/fabrication)
    all_eol_scrap_list = all_eolscrap.index.to_numpy()
    all_forming_scrap_list = forming_scrap_by_alloy.index.to_numpy()
    all_fabrication_scrap_list = fabrication_scrap_by_alloy.index.to_numpy()

    # Set furnace charge constraint and mix fraction for each product (product_parameters can store other product-dependent variables if needed)
    process_yield = []
    alpha_furnace = []
    mix_forming_fraction = []
    mix_fabrication_fraction = []
    mix_eol_fraction = []
    # Loop through each product in the product list to extract furnace charge constraint and mix fraction
    for i in product_list:
        result = product_parameters.query('Alloy== @i')["Process Yield"]
        if result.empty:
            raise ValueError(f"Product '{i}' not found in product_parameters. Check that all products in demand data exist in product_parameters file.")
        process_yield.append(result)
        
        result = product_parameters.query('Alloy== @i')["Furnace Charge Constraint"]
        if result.empty:
            raise ValueError(f"Product '{i}' not found in product_parameters. Check that all products in demand data exist in product_parameters file.")
        alpha_furnace.append(result)
    for i in all_forming_scrap_list:
        result = product_parameters.query('Alloy== @i')["Mix Forming Fraction"]
        if result.empty:
            raise ValueError(f"Forming scrap alloy '{i}' not found in product_parameters. Check that all forming scrap alloys exist in product_parameters file.")
        mix_forming_fraction.append(result)
    for i in all_fabrication_scrap_list:
        result = product_parameters.query('Alloy== @i')["Mix Fabrication Fraction"]
        if result.empty:
            raise ValueError(f"Fabrication scrap alloy '{i}' not found in product_parameters. Check that all fabrication scrap alloys exist in product_parameters file.")
        mix_fabrication_fraction.append(result)
    for i in all_eol_scrap_list:
        result = product_parameters.query('Alloy== @i')["Mix EOL Fraction"]
        if result.empty:
            raise ValueError(f"EOL scrap alloy '{i}' not found in product_parameters. Check that all EOL scrap alloys exist in product_parameters file.")
        mix_eol_fraction.append(result)
    process_yield = pd.concat(process_yield)
    alpha_furnace = pd.concat(alpha_furnace).to_numpy()
    mix_forming_fraction = pd.concat(mix_forming_fraction)
    mix_fabrication_fraction = pd.concat(mix_fabrication_fraction)
    mix_eol_fraction = pd.concat(mix_eol_fraction)

    # Set furnace and scrap processing yields
    # Furnace yield: how much material is retained in reverbatory furnace
    furnace_yield = scenario_row["furnace_yield"].values[0]
    # Scrap processing yield: how much scrap is retained during scrap processing (eddy current, magnetic, etc. separation)
    scrap_proc_yield = scenario_row["scrap_proc_yield"].values[0]

    # Set index of scrap source being separated
    scrapsep_index = np.array(json.loads(scenario_row["scrapsep_index"].values[0]), dtype=int)

    # Set scenario of forming and fabrication scrap mixing
    forming_mix_scenario = scenario_row["forming_mix_scenario"].values[0]
    fabrication_mix_scenario = scenario_row["fabrication_mix_scenario"].values[0]
    eol_mix_scenario = scenario_row["eol_mix_scenario"].values[0]

    # Set number of products (extracts from list of products)
    num_products = len(product_list)

    # Set number of elements
    elements_list = compositions_data.columns.values
    num_elements = len(elements_list)

    # Convert ingot demand from dataframe to numpy array
    demand = demand.to_numpy()


    # Build to_mix lists and labels for forming, fabrication, and EOL scrap mixing
    # Pass both product_list (for optimization) and all_scrap_list (for mixing all streams)
    forming_to_mix = build_mix_list(product_list, forming_mix_scenario, all_forming_scrap_list)
    fabrication_to_mix = build_mix_list(product_list, fabrication_mix_scenario, all_fabrication_scrap_list)
    eol_to_mix = build_mix_list(product_list, eol_mix_scenario, all_eol_scrap_list)

    # Mix forming, fabrication, and EOL scrap streams and store quantities and compositions
    # Returns product_list_quantities and has_product_list_alloys
    mixed_forming_quantities, mixed_forming_comps, forming_product_list_quantities, forming_has_product_list = mix(
        forming_to_mix, forming_scrap_by_alloy, compositions_data, mix_forming_fraction, product_list, scenario, year
    )
    mixed_fabrication_quantities, mixed_fabrication_comps, fabrication_product_list_quantities, fabrication_has_product_list = mix(
        fabrication_to_mix, fabrication_scrap_by_alloy, compositions_data, mix_fabrication_fraction, product_list, scenario, year
    )
    mixed_eol_quantities, mixed_eol_comps, eol_product_list_quantities, eol_has_product_list = mix(
        eol_to_mix, all_eolscrap, compositions_data, mix_eol_fraction, product_list, scenario, year
    )

    # Get labels for mixed forming, fabrication, and EOL scrap streams
    mixed_forming_labels = [stream["label"] for stream in forming_to_mix if "label" in stream]
    mixed_fabrication_labels = [stream["label"] for stream in fabrication_to_mix if "label" in stream]
    mixed_eol_labels = [stream["label"] for stream in eol_to_mix if "label" in stream]
    
    # Filter mixed streams to only include those with product_list contributions
    # and adjust quantities to only count product_list volumes
    if mixed_forming_quantities.size > 0:
        mixed_forming_quantities, mixed_forming_comps, mixed_forming_labels = filter_scrap_for_optimization(
            mixed_forming_quantities, mixed_forming_comps, mixed_forming_labels, forming_product_list_quantities, forming_has_product_list
        )
    if mixed_fabrication_quantities.size > 0:
        mixed_fabrication_quantities, mixed_fabrication_comps, mixed_fabrication_labels = filter_scrap_for_optimization(
            mixed_fabrication_quantities, mixed_fabrication_comps, mixed_fabrication_labels, fabrication_product_list_quantities, fabrication_has_product_list
        )
    if mixed_eol_quantities.size > 0:
        mixed_eol_quantities, mixed_eol_comps, mixed_eol_labels = filter_scrap_for_optimization(
            mixed_eol_quantities, mixed_eol_comps, mixed_eol_labels, eol_product_list_quantities, eol_has_product_list
        )

    # Store new mixed scrap streams in DataFrames (when to_mix is empty, no streams are mixed)
    if mixed_forming_quantities.size == 0:
        # If no forming scrap streams are mixed, use the original forming scrap by alloy data
        # Filter to only include product_list alloys
        forming_scrap_df = forming_scrap_by_alloy.copy()
        product_list_alloys_in_forming = [alloy for alloy in product_list if alloy in forming_scrap_df.index]
        forming_scrap_df = forming_scrap_df.reindex(product_list_alloys_in_forming)
        
        # Change the index name and add ' forming scrap' to each index value
        forming_scrap_df.index = forming_scrap_df.index + ' forming scrap'
        forming_scrap_df.index.name = 'Forming scrap streams'
        # Find compositions of forming scrap streams in compositions_data by creating a mask for each all forming scrap alloys that appear in list
        mask = compositions_data.index.str.endswith('midrange') & compositions_data.index.map(
            lambda idx: any(alloy in str(idx) for alloy in product_list_alloys_in_forming)
        )
        forming_scrap_comps = compositions_data.loc[mask]
        forming_scrap_comps.index = forming_scrap_comps.index.str.replace('midrange', 'forming scrap')
        forming_scrap_comps = forming_scrap_comps.fillna(0)
    else:
        forming_scrap_df = pd.Series(mixed_forming_quantities, index=pd.Index(mixed_forming_labels, name='Forming scrap streams'), name=year)
        forming_scrap_comps = pd.DataFrame(mixed_forming_comps, index=mixed_forming_labels, columns=compositions_data.columns)
    if mixed_fabrication_quantities.size == 0:
        # Filter to only include product_list alloys
        fabrication_scrap_df = fabrication_scrap_by_alloy.copy()
        product_list_alloys_in_fabrication = [alloy for alloy in product_list if alloy in fabrication_scrap_df.index]
        fabrication_scrap_df = fabrication_scrap_df.reindex(product_list_alloys_in_fabrication)
        
        fabrication_scrap_df.index = fabrication_scrap_df.index + ' fabrication scrap'
        fabrication_scrap_df.index.name = 'Fabrication scrap streams'
        mask = compositions_data.index.str.endswith('midrange') & compositions_data.index.map(
            lambda idx: any(alloy in idx for alloy in product_list_alloys_in_fabrication)
        )
        fabrication_scrap_comps = compositions_data.loc[mask]
        fabrication_scrap_comps.index = fabrication_scrap_comps.index.str.replace('midrange', 'fabrication scrap')
        fabrication_scrap_comps = fabrication_scrap_comps.fillna(0)
    else:
        fabrication_scrap_df = pd.Series(mixed_fabrication_quantities, index=pd.Index(mixed_fabrication_labels, name='Fabrication scrap streams'), name=year)
        fabrication_scrap_comps = pd.DataFrame(mixed_fabrication_comps, index=mixed_fabrication_labels, columns=compositions_data.columns)
    if mixed_eol_quantities.size == 0:
        # For individual alloy scenarios, filter to only include product_list alloys
        eol_scrap_df = all_eolscrap.copy()
        
        # Filter to only include alloys from product_list
        product_list_alloys_in_eol = [alloy for alloy in product_list if alloy in eol_scrap_df.index]
        eol_scrap_df = eol_scrap_df.reindex(product_list_alloys_in_eol)
        
        print(f"Filtered individual EOL streams to {len(eol_scrap_df)} alloys (from product_list)")
        
        eol_scrap_df.index = eol_scrap_df.index + ' EOL scrap'
        eol_scrap_df.index.name = 'EOL scrap streams'
        # Find compositions of forming scrap streams in compositions_data by creating a mask for each all forming scrap alloys that appear in list
        mask = compositions_data.index.str.endswith('midrange') & compositions_data.index.map(
            lambda idx: any(alloy in idx for alloy in product_list_alloys_in_eol)
        )
        eol_scrap_comps = compositions_data.loc[mask]
        eol_scrap_comps.index = eol_scrap_comps.index.str.replace('midrange', 'EOL scrap')
        eol_scrap_comps = eol_scrap_comps.fillna(0)
        print("EOL scrap quantities before applying separation accuracy:\n", eol_scrap_df)
        # Apply separation accuracy for individual stream scenarios
        if "93% accuracy" in eol_mix_scenario:
            print("Applying 93% separation accuracy to individual EOL scrap streams")
            eol_scrap_df, eol_scrap_comps = apply_separation_accuracy(eol_scrap_df, eol_scrap_comps, product_list, 0.93, compositions_data, eol_mix_scenario=eol_mix_scenario)
            print("EOL scrap quantities after applying separation accuracy:\n", eol_scrap_df)
        elif "100% accuracy" in eol_mix_scenario:
            print("Applying 100% separation accuracy to individual EOL scrap streams")
            eol_scrap_df, eol_scrap_comps = apply_separation_accuracy(eol_scrap_df, eol_scrap_comps, product_list, 1.0, compositions_data, eol_mix_scenario=eol_mix_scenario)
            print("EOL scrap quantities after applying separation accuracy:\n", eol_scrap_df)
        elif "88% accuracy" in eol_mix_scenario:
            print("Applying 88% separation accuracy to individual EOL scrap streams")
            eol_scrap_df, eol_scrap_comps = apply_separation_accuracy(eol_scrap_df, eol_scrap_comps, product_list, 0.88, compositions_data, eol_mix_scenario=eol_mix_scenario)
            print("EOL scrap quantities after applying separation accuracy:\n", eol_scrap_df)
    else:
        # For grouped scenarios, use mixed stream data
        if "93% accuracy" in eol_mix_scenario:
            print("Applying 93% separation accuracy to EOL scrap streams")
            separation_acc = 0.93
            eol_scrap_df, eol_scrap_comps = apply_separation_accuracy(
                None, None, product_list, separation_acc, compositions_data,
                mixed_eol_quantities=mixed_eol_quantities,
                mixed_eol_comps=mixed_eol_comps,
                mixed_eol_labels=mixed_eol_labels,
                eol_to_mix=eol_to_mix,
                eol_mix_scenario=eol_mix_scenario
            )
            print("EOL scrap quantities after applying separation accuracy:\n", eol_scrap_df)
        elif "100% accuracy" in eol_mix_scenario:
            print("Applying 100% separation accuracy to EOL scrap streams")
            separation_acc = 1.0
            eol_scrap_df, eol_scrap_comps = apply_separation_accuracy(
                None, None, product_list, separation_acc, compositions_data,
                mixed_eol_quantities=mixed_eol_quantities,
                mixed_eol_comps=mixed_eol_comps,
                mixed_eol_labels=mixed_eol_labels,
                eol_to_mix=eol_to_mix,
                eol_mix_scenario=eol_mix_scenario
            )
            print("EOL scrap quantities after applying separation accuracy:\n", eol_scrap_df)
        elif "88% accuracy" in eol_mix_scenario:
            print("Applying 88% separation accuracy to EOL scrap streams")
            separation_acc = 0.88
            eol_scrap_df, eol_scrap_comps = apply_separation_accuracy(
                None, None, product_list, separation_acc, compositions_data,
                mixed_eol_quantities=mixed_eol_quantities,
                mixed_eol_comps=mixed_eol_comps,
                mixed_eol_labels=mixed_eol_labels,
                eol_to_mix=eol_to_mix,
                eol_mix_scenario=eol_mix_scenario
            )
            print("EOL scrap quantities after applying separation accuracy:\n", eol_scrap_df)
        else:
            # Regular mixed stream scenarios (baseline EOL, no contamination, etc.)
            eol_scrap_df = pd.Series(mixed_eol_quantities, index=pd.Index(mixed_eol_labels, name='EOL scrap streams'), name=year)
            eol_scrap_comps = pd.DataFrame(mixed_eol_comps, index=mixed_eol_labels, columns=compositions_data.columns)

    # Add new mixed streams to compositions_data and supply_df
    compositions_data = pd.concat([compositions_data, forming_scrap_comps, fabrication_scrap_comps, eol_scrap_comps])

    supply_df = pd.concat([forming_scrap_df, fabrication_scrap_df, eol_scrap_df])

    supply = supply_df.to_numpy()
    
    # Track where EOL scrap starts in the supply array (for emissions calculation)
    num_forming_streams = len(forming_scrap_df)
    num_fabrication_streams = len(fabrication_scrap_df)
    eol_scrap_start_index = num_forming_streams + num_fabrication_streams

    # Calculate total number of scrap streams
    num_scrap = len(supply)

    if separation_data.shape[0] > 0:
        # PLACEHOLDER FOR FUNCTION TO SEPARATE SCRAP STREAMS
        # Should return num_sep (number of separated streams), SS_comp (separated stream compositions), and fractions (2D list of fractions)
        pass
    else:
        nonsep_index = np.arange(num_scrap)
        num_sep = 0
        fractions = []
        SS_comp = np.array([])
    
    # Total number of scrap sources (including separated streams)
    scrap_source = num_scrap + num_sep

    # Generate 2d array containing compositions of all scrap streams
    scrap_comp_df = pd.concat(
        [forming_scrap_comps, fabrication_scrap_comps, eol_scrap_comps],
        axis=0
    )

    if SS_comp.size > 0:
        scrap_comp_df = pd.concat([scrap_comp_df, SS_comp], axis=0)
    
    # ********** For LIBS fixed comps scenario: override Twitch-5xxx-PC1 and Twitch-6xxx-PC1 compositions **********
    if "LIBS fixed comps" in scenario:
        # Override Twitch-5xxx-PC1 composition
        if "Twitch-5xxx-PC1" in scrap_comp_df.index:
            scrap_comp_df.loc["Twitch-5xxx-PC1", "Si"] = 0.86
            scrap_comp_df.loc["Twitch-5xxx-PC1", "Fe"] = 0.365
            scrap_comp_df.loc["Twitch-5xxx-PC1", "Cu"] = 0.255
            scrap_comp_df.loc["Twitch-5xxx-PC1", "Mn"] = 0.175
            scrap_comp_df.loc["Twitch-5xxx-PC1", "Mg"] = 2.7
            scrap_comp_df.loc["Twitch-5xxx-PC1", "Zn"] = 0.15
            print("TESTING: Overrode Twitch-5xxx-PC1 composition for LIBS fixed comps scenario")
        
        # Override Twitch-6xxx-PC1 composition
        if "Twitch-6xxx-PC1" in scrap_comp_df.index:
            scrap_comp_df.loc["Twitch-6xxx-PC1", "Si"] = 0.69
            scrap_comp_df.loc["Twitch-6xxx-PC1", "Fe"] = 0.31
            scrap_comp_df.loc["Twitch-6xxx-PC1", "Cu"] = 0.34
            scrap_comp_df.loc["Twitch-6xxx-PC1", "Mn"] = 0.175
            scrap_comp_df.loc["Twitch-6xxx-PC1", "Mg"] = 0.545
            scrap_comp_df.loc["Twitch-6xxx-PC1", "Zn"] = 0.06
            print("TESTING: Overrode Twitch-6xxx-PC1 composition for LIBS fixed comps scenario")
    
    scrap_comp = scrap_comp_df.to_numpy()

    # Extract composition bounds for each product
    compositions_lower = []
    compositions_upper = []
    for i in product_list:
        # Extract the composition bounds for the product
        compositions_lower.append(compositions_data.loc[compositions_data.index.str.endswith('lower') & compositions_data.index.str.startswith(i)])
        compositions_upper.append(compositions_data.loc[compositions_data.index.str.endswith('upper') & compositions_data.index.str.startswith(i)])
    comp_lower = pd.concat(compositions_lower).to_numpy()
    comp_upper = pd.concat(compositions_upper).to_numpy()

    # Extract compositions for primary inputs
    prim_al_comp_df = compositions_data.loc[compositions_data.index.str.contains('Primary') & compositions_data.index.str.contains(scenario_row["prim_comp"].values[0])]

    # Filter for primary alloying elements
    prim_alloying_comps_df = compositions_data.loc[compositions_data.index.str.contains('Primary') & compositions_data.index.str.contains('|'.join(elements_list), regex=True)]

    # Combine primary aluminum composition with primary alloying compositions
    prim_comps_df = pd.concat([prim_al_comp_df, prim_alloying_comps_df])
    prim_comp = prim_comps_df.to_numpy()

    # Find Twitch composition in new compositions dataframe
    twitch_comp = compositions_data.loc[compositions_data.index.str.contains('Twitch')]
    print("Twitch composition:\n", twitch_comp)

    print("Input values generated.")

    input_dict = {
        "num_products": num_products,
        "num_elements": num_elements,
        "product_list": product_list,
        "elements_list": elements_list,
        "scrap_source": scrap_source,
        "num_scrap": num_scrap,
        "scrapsep_index": scrapsep_index,
        "num_sep": num_sep,
        "supply": supply,
        "nonsep_index": nonsep_index,
        "furnace_yield": furnace_yield,
        "demand": demand,
        "fractions": fractions,
        "num_temps": num_temps,
        "prim_comp": prim_comp,
        "prim_comp_df": prim_comps_df,
        "comp_lower": comp_lower,
        "comp_upper": comp_upper,
        "scrap_comp": scrap_comp,
        "scrap_comp_df": scrap_comp_df,
        "alpha_furnace": alpha_furnace,
        "emissions_data_frozen": emissions_data_frozen,
        "emissions_data_moderate": emissions_data_moderate,
        "emissions_data_aggressive": emissions_data_aggressive,
        "forming_scrap_df": forming_scrap_df,
        "forming_scrap_comps": forming_scrap_comps,
        "fabrication_scrap_df": fabrication_scrap_df,
        "fabrication_scrap_comps": fabrication_scrap_comps,
        "eol_scrap_df": eol_scrap_df,
        "eol_scrap_comps": eol_scrap_comps,
        "mix_forming_fraction": mix_forming_fraction,
        "mix_fabrication_fraction": mix_fabrication_fraction,
        "mix_eol_fraction": mix_eol_fraction,
        "eol_scrap_start_index": eol_scrap_start_index
    }

    return input_dict

def apply_separation_accuracy(eol_scrap_df, eol_scrap_comps, product_list, separation_accuracy, compositions_data=None, mixed_eol_quantities=None, mixed_eol_comps=None, mixed_eol_labels=None, eol_to_mix=None, eol_mix_scenario=None):
    """Function that applies separation accuracy to EOL scrap streams
    
    Args:
        eol_scrap_df: DataFrame with EOL scrap quantities (for individual streams mode)
        eol_scrap_comps: DataFrame with EOL scrap compositions (for individual streams mode)
        product_list: List of product alloys
        separation_accuracy: Float between 0 and 1 (e.g., 0.93 for 93% accuracy)
        compositions_data: DataFrame with compositions (needed for grouped mode)
        mixed_eol_quantities: Array of mixed stream quantities (for grouped mode)
        mixed_eol_comps: Array of mixed stream compositions (for grouped mode)
        mixed_eol_labels: List of mixed stream labels (for grouped mode)
        eol_to_mix: List of mixing instructions with separation groups (for grouped mode)
        eol_mix_scenario: String describing the scenario (for special case detection)

    Returns:
        new_quantities: DataFrame with new EOL scrap quantities after applying separation accuracy
        new_compositions: DataFrame with new EOL scrap compositions after applying separation accuracy
    """
    # Define separation accuracy and misclassification rates
    misclassification_rate = 1 - separation_accuracy
    
    # Special case: fully separated streams with contamination (when to_mix is empty but contamination needed)
    if (eol_mix_scenario and 
        "accuracy separation" in eol_mix_scenario and 
        "no contamination" not in eol_mix_scenario and
        (mixed_eol_quantities is None or len(mixed_eol_quantities) == 0) and
        eol_scrap_df is not None):
        
        print("Applying contamination to fully separated EOL scrap streams based on baseline parent groups")
        
        # Get baseline parent groups to determine contamination sources
        baseline_groups = build_mix_list(product_list, "Baseline PC")
        
        # Calculate contamination factors dynamically based on Twitch composition
        contamination_factors_C = calculate_contamination_factors(product_list, compositions_data)
        
        # Create copies to modify
        new_quantities = eol_scrap_df.copy()
        new_compositions = eol_scrap_comps.copy()
        
        # Apply contamination to each individual EOL scrap stream
        for alloy_stream in eol_scrap_df.index:
            alloy_name = alloy_stream.replace(' EOL scrap', '')
            
            # Find which baseline group this alloy belongs to and get contamination level
            parent_group_alloys = None
            contamination_level = None
            for group in baseline_groups:
                if alloy_name in group["alloys"]:
                    parent_group_alloys = group["alloys"]
                    contamination_level = group.get("contamination", "A")
                    break
            
            if parent_group_alloys and len(parent_group_alloys) > 1:
                # Calculate contaminated quantity (separation accuracy logic)
                original_qty = eol_scrap_df[alloy_stream]
                correct_portion = separation_accuracy * original_qty
                contamination_from_others = 0

                # Add contamination from other alloys in the same baseline group
                for other_alloy in parent_group_alloys:
                    if other_alloy != alloy_name:
                        other_stream = other_alloy + ' EOL scrap'
                        if other_stream in eol_scrap_df.index:
                            contamination_from_others += (misclassification_rate / (len(parent_group_alloys) - 1)) * eol_scrap_df[other_stream]

                new_quantities[alloy_stream] = correct_portion + contamination_from_others
                
                # Calculate contaminated composition
                if new_quantities[alloy_stream] > 0:
                    # Get original composition for this alloy
                    comp_mask = eol_scrap_comps.index.str.startswith(alloy_name)
                    if comp_mask.any():
                        weighted_comp = (correct_portion / new_quantities[alloy_stream]) * eol_scrap_comps.loc[comp_mask].iloc[0]
                        
                        # Add weighted composition from contaminating alloys (separation accuracy contamination)
                        for other_alloy in parent_group_alloys:
                            if other_alloy != alloy_name:
                                other_stream = other_alloy + ' EOL scrap'
                                other_comp_mask = eol_scrap_comps.index.str.startswith(other_alloy)
                                if other_comp_mask.any():
                                    contamination_qty = (misclassification_rate / (len(parent_group_alloys) - 1)) * eol_scrap_df[other_stream]
                                    weighted_comp += (contamination_qty / new_quantities[alloy_stream]) * eol_scrap_comps.loc[other_comp_mask].iloc[0]

                        # Apply external contamination factors based on contamination level
                        if contamination_level in ["B", "C"]:
                            element_columns = compositions_data.columns.tolist()
                            
                            for element, factor_C in contamination_factors_C.items():
                                if element in element_columns:
                                    element_idx = element_columns.index(element)
                                    
                                    if contamination_level == "C":
                                        # Apply full contamination factor for level C
                                        contamination_factor = factor_C
                                    elif contamination_level == "B":
                                        # Apply half contamination factor for level B
                                        contamination_factor = factor_C / 2
                                    
                                    # Increase the composition by the contamination factor
                                    # Only apply if the current composition is not NaN
                                    if not pd.isna(weighted_comp.iloc[element_idx]):
                                        weighted_comp.iloc[element_idx] *= (1 + contamination_factor)

                        new_compositions.loc[comp_mask] = weighted_comp.values
                    else:
                        print(f"Original composition not found for {alloy_name}")
            else:
                # For streams not in multi-alloy groups, still apply external contamination
                if contamination_level in ["B", "C"]:
                    comp_mask = eol_scrap_comps.index.str.startswith(alloy_name)
                    if comp_mask.any():
                        weighted_comp = eol_scrap_comps.loc[comp_mask].iloc[0].copy()
                        element_columns = compositions_data.columns.tolist()
                        
                        for element, factor_C in contamination_factors_C.items():
                            if element in element_columns:
                                element_idx = element_columns.index(element)
                                
                                if contamination_level == "C":
                                    contamination_factor = factor_C
                                elif contamination_level == "B":
                                    contamination_factor = factor_C / 2
                                
                                if not pd.isna(weighted_comp.iloc[element_idx]):
                                    weighted_comp.iloc[element_idx] *= (1 + contamination_factor)
                        
                        new_compositions.loc[comp_mask] = weighted_comp.values

        return new_quantities, new_compositions
    
    # Check if grouped mode (new scenarios) or individual mode
    if mixed_eol_quantities is not None and eol_to_mix is not None:
        # Apply separation accuracy to grouped streams with family separation
        print("Using grouped separation mode with alloy family separation")

        # Create DataFrames from mixed streams
        if compositions_data is None:
            raise ValueError("compositions_data must be provided in grouped mode to set columns for mixed_eol_comps DataFrame.")
        new_quantities = pd.Series(mixed_eol_quantities, index=pd.Index(mixed_eol_labels, name='EOL scrap streams'), name='quantities')
        new_compositions = pd.DataFrame(mixed_eol_comps, index=mixed_eol_labels, columns=compositions_data.columns)

        # Group streams by their separation group
        separation_groups = {}
        for stream in eol_to_mix:
            if "separation_group" in stream:
                group_name = stream["separation_group"]
                if group_name not in separation_groups:
                    separation_groups[group_name] = []
                separation_groups[group_name].append(stream["label"])

        # Apply separation accuracy within each group
        for group_name, stream_labels in separation_groups.items():
            group_streams = [label for label in stream_labels if label in new_quantities.index]

            if len(group_streams) > 1:  # Only apply separation logic if there are multiple streams in the group
                # Store original quantities and compositions for this group
                original_quantities = {}
                original_compositions = {}

                for stream_label in group_streams:
                    original_quantities[stream_label] = new_quantities[stream_label]
                    original_compositions[stream_label] = new_compositions.loc[stream_label].copy()

                # Calculate contaminated quantities and compositions for each stream in the group
                for i, stream_i in enumerate(group_streams):
                    original_qty_i = original_quantities[stream_i]

                    # Calculate new quantity: X% stays correct + contamination from others
                    correct_portion = separation_accuracy * original_qty_i
                    contamination_from_others = 0

                    # Calculate contamination from other streams in the same separation group
                    for j, stream_j in enumerate(group_streams):
                        if i != j:
                            original_qty_j = original_quantities[stream_j]
                            contamination_from_others += (misclassification_rate / (len(group_streams) - 1)) * original_qty_j

                    new_quantities[stream_i] = correct_portion + contamination_from_others

                    # Calculate contaminated composition
                    if new_quantities[stream_i] > 0:
                        # Start with the correct portion weighted by its fraction
                        weighted_comp = (correct_portion / new_quantities[stream_i]) * original_compositions[stream_i]

                        # Add contamination from other streams in the group
                        for j, stream_j in enumerate(group_streams):
                            if i != j:
                                contamination_qty_j = (misclassification_rate / (len(group_streams) - 1)) * original_quantities[stream_j]
                                weighted_comp += (contamination_qty_j / new_quantities[stream_i]) * original_compositions[stream_j]

                        # Update the composition in the DataFrame
                        new_compositions.loc[stream_i] = weighted_comp

            elif len(group_streams) == 1:
                print(f"Group {group_name} has only one stream ({group_streams[0]}), no separation needed")
            else:
                print(f"Group {group_name} has no streams in the filtered list, skipping")

        return new_quantities, new_compositions
    
    else:
        # Apply separation accuracy to individual alloy streams
        print("Using individual separation mode")
        
        # Get the baseline EOL groupings from build_mix_list
        baseline_eol_groups = build_mix_list(product_list, "Baseline PC")
        
        # Initialize dataframes for new quantities and compositions
        new_quantities = eol_scrap_df.copy()
        new_compositions = eol_scrap_comps.copy()
        
        # Process each group from baseline EOL scenario
        for group in baseline_eol_groups:
            group_alloys = [alloy + ' EOL scrap' for alloy in group["alloys"] if alloy + ' EOL scrap' in eol_scrap_df.index]
            
            if len(group_alloys) > 1:  # Only apply separation logic if there are multiple alloys in the group
                # Calculate contaminated quantities for each alloy in the group
                for i, alloy_i in enumerate(group_alloys):
                    if alloy_i in eol_scrap_df.index:
                        original_qty_i = eol_scrap_df[alloy_i]
                        
                        # Calculate new quantity: X% stays correct + contamination from others
                        correct_portion = separation_accuracy * original_qty_i
                        contamination_from_others = 0
                        
                        # Calculate contamination from other alloys in the group and add to the new quantity
                        for j, alloy_j in enumerate(group_alloys):
                            if i != j and alloy_j in eol_scrap_df.index:
                                original_qty_j = eol_scrap_df[alloy_j]
                                contamination_from_others += (misclassification_rate / (len(group_alloys) - 1)) * original_qty_j
                        
                        new_quantities[alloy_i] = correct_portion + contamination_from_others
                        
                        # Calculate contaminated composition
                        if new_quantities[alloy_i] > 0:
                            # Get original composition indices (remove ' EOL scrap' suffix)
                            original_alloy_name_i = alloy_i.replace(' EOL scrap', '')
                            
                            # Find composition for alloy i
                            comp_mask_i = eol_scrap_comps.index.str.contains(original_alloy_name_i, regex=False)
                            if comp_mask_i.any():
                                base_comp_i = eol_scrap_comps.loc[comp_mask_i].iloc[0]
                                
                                # Calculate weighted composition
                                weighted_comp = (correct_portion / new_quantities[alloy_i]) * base_comp_i
                                
                                for j, alloy_j in enumerate(group_alloys):
                                    if i != j and alloy_j in eol_scrap_df.index:
                                        original_alloy_name_j = alloy_j.replace(' EOL scrap', '')
                                        comp_mask_j = eol_scrap_comps.index.str.contains(original_alloy_name_j, regex=False)
                                        if comp_mask_j.any():
                                            base_comp_j = eol_scrap_comps.loc[comp_mask_j].iloc[0]
                                            contamination_qty_j = (misclassification_rate / (len(group_alloys) - 1)) * eol_scrap_df[alloy_j]
                                            weighted_comp += (contamination_qty_j / new_quantities[alloy_i]) * base_comp_j
                                        else:
                                            print(f"ERROR: Could not find composition for {original_alloy_name_j}")
                                
                                # Find the correct index in new_compositions that contains alloy_i
                                matching_indices = new_compositions.index[new_compositions.index.str.contains(original_alloy_name_i, regex=False)]
                                if len(matching_indices) > 0:
                                    correct_index = matching_indices[0]
                                    new_compositions.loc[correct_index] = weighted_comp
                                else:
                                    print(f"ERROR: Could not find index containing {original_alloy_name_i} in new_compositions")
        
        return new_quantities, new_compositions

def calculate_kelly_contamination_factors(product_list, compositions_data, year=2016):
    """Calculate contamination factors for Kelly scenarios with separate Twitch-5xxx-PC1 and Twitch-6xxx-PC1.
    Uses "93% accuracy PC scrap separation by family and no contamination" as baseline.
    
    Args:
        product_list: List of product alloys
        compositions_data: DataFrame containing composition data
        year: Year for baseline (default 2016)
    
    Returns:
        dict: Dictionary with keys 'Twitch-5xxx-PC1' and 'Twitch-6xxx-PC1', each containing contamination factors for elements
    """
    # Target compositions - Kelly (2018) measured compositions for 5xxx and 6xxx (to compare against 2016 baseline year)
    target_5xxx = {
        'Si': 0.86,
        'Fe': 0.365,
        'Cu': 0.255,
        'Mn': 0.175,
        'Mg': 2.7,
        'Zn': 0.15
    }
    
    target_6xxx = {
        'Si': 0.69,
        'Fe': 0.31,
        'Cu': 0.34,
        'Mn': 0.175,
        'Mg': 0.545,
        'Zn': 0.06
    }
    
    # Load baseline data with "93% accuracy PC scrap separation by family and no contamination"
    loaded_data_baseline = load_data(year, "Baseline")
    supply_data_baseline = loaded_data_baseline["supply_data"]
    collection_rates_data_baseline = loaded_data_baseline["collection_rates_data"]
    scenario_row_baseline = loaded_data_baseline["scenario_row"]
    
    # Get full list of ALL scrap alloys from supply data
    all_eol_scrap_list_baseline = supply_data_baseline[year].index.to_numpy()
    
    # Get EOL groups from build_mix_list for this scenario
    baseline_eol_groups = build_mix_list(product_list, "93% accuracy PC scrap separation by family", all_eol_scrap_list_baseline)
    
    # Find 5xxx and 6xxx alloy groups
    twitch_5xxx_alloys = None
    twitch_6xxx_alloys = None
    for group in baseline_eol_groups:
        if group["label"] == "Twitch-5xxx-PC1":
            twitch_5xxx_alloys = group["alloys"]
        elif group["label"] == "Twitch-6xxx-PC1":
            twitch_6xxx_alloys = group["alloys"]
    
    if twitch_5xxx_alloys is None or twitch_6xxx_alloys is None:
        raise ValueError("Could not find Twitch-5xxx-PC1 or Twitch-6xxx-PC1 groups in baseline scenario")
    
    # Get EOL scrap for the specified year
    all_eolscrap_generated_baseline = supply_data_baseline[year]
    collection_rates_baseline = collection_rates_data_baseline
    all_eolscrap_baseline = all_eolscrap_generated_baseline.copy()
    
    # Apply collection rates
    for alloy in all_eolscrap_generated_baseline.index:
        if alloy in collection_rates_baseline.index:
            collection_rate_year = float(collection_rates_baseline.loc[alloy, year])
            scrap_proc_yield = scenario_row_baseline["scrap_proc_yield"].values[0]
            all_eolscrap_baseline.loc[alloy] = all_eolscrap_generated_baseline.loc[alloy] * collection_rate_year * scrap_proc_yield
        else:
            scrap_proc_yield = scenario_row_baseline["scrap_proc_yield"].values[0]
            all_eolscrap_baseline.loc[alloy] = all_eolscrap_generated_baseline.loc[alloy] * scrap_proc_yield
    
    # Function to calculate contamination factors for a group
    def calc_factors_for_group(twitch_alloys, target_composition):
        compositions = []
        weights = []
        
        for alloy in twitch_alloys:
            comp_mask = compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(alloy)
            if comp_mask.any():
                alloy_comp = compositions_data.loc[comp_mask].iloc[0]
                compositions.append(alloy_comp)
                if alloy in all_eolscrap_baseline.index:
                    weights.append(all_eolscrap_baseline[alloy])
                else:
                    weights.append(0.0)
        
        if not compositions:
            raise ValueError(f"Could not find compositions for alloys: {twitch_alloys}")
        
        weights = np.array(weights)
        total_weight = weights.sum()
        
        if total_weight > 0:
            to_mix_compositions = np.vstack([comp.fillna(0).to_numpy() for comp in compositions])
            weights_broadcast = np.broadcast_to(weights[:, None], to_mix_compositions.shape)
            theoretical_comp_array = np.ma.average(to_mix_compositions, axis=0, weights=weights_broadcast).filled(0.0)
            theoretical_comp = pd.Series(theoretical_comp_array, index=compositions_data.columns)
        else:
            raise ValueError(f"Total weight for alloys is zero: {twitch_alloys}")
        
        # Calculate contamination factors
        contamination_factors = {}
        percent_differences = []
        
        for element in target_composition:
            if element in theoretical_comp.index:
                theoretical_value = theoretical_comp[element]
                target_value = target_composition[element]
                
                if theoretical_value > 0:
                    percent_diff = (target_value - theoretical_value) / theoretical_value
                    contamination_factors[element] = percent_diff
                    percent_differences.append(abs(percent_diff))
                else:
                    contamination_factors[element] = target_value
                    percent_differences.append(target_value)
        
        # Calculate average for Cr, Ni, Ti
        avg_contamination = np.mean(percent_differences) if percent_differences else 0.0
        for element in ['Cr', 'Ni', 'Ti']:
            contamination_factors[element] = avg_contamination
        
        return contamination_factors
    
    # Calculate factors for both groups
    factors_5xxx = calc_factors_for_group(twitch_5xxx_alloys, target_5xxx)
    factors_6xxx = calc_factors_for_group(twitch_6xxx_alloys, target_6xxx)
    
    return {
        'Twitch-5xxx-PC1': factors_5xxx,
        'Twitch-6xxx-PC1': factors_6xxx
    }

def calculate_contamination_factors(product_list, compositions_data, year=2016, target_composition=None):
    """Calculate contamination factors for level C based on percent difference 
    between theoretical uncontaminated Twitch composition and target contaminated composition.
    
    Args:
        product_list: List of product alloys
        compositions_data: DataFrame containing composition data
        year: Year for baseline (default 2016, can be changed to 2024 or other years)
        target_composition: Optional dict with target composition. If None, uses Kelly Twitch equivalent
    
    Returns:
        dict: Contamination factors for each element
    """
    
    # Target contaminated composition (Kelly Twitch equivalent) - used as default
    if target_composition is None:
        target_composition = {
            'Si': 5.100,
            'Fe': 0.600, 
            'Cu': 1.600,
            'Mn': 0.300,
            'Mg': 1.900,
            'Zn': 1.000
        }
    
    # print(f"Target composition: {target_composition}")
    elif target_composition == "F-150":
        # F-150 shredding trial (UM) composition
        target_composition = {
            'Si': 3.669,
            'Fe': 0.534, 
            'Cu': 1.160,
            'Mn': 0.331,
            'Mg': 2.324,
            'Zn': 0.606
        }
    
    # Get EOL scrap quantities for weighting - need to load data for the specified year
    loaded_data_baseline = load_data(year, "Baseline")  # Use Baseline scenario
    supply_data_baseline = loaded_data_baseline["supply_data"]
    
    # Get full list of ALL scrap alloys from supply data (not just product_list)
    all_eol_scrap_list_baseline = supply_data_baseline[year].index.to_numpy()
    
    # Get Twitch alloys from build_mix_list for Baseline EOL scenario
    # IMPORTANT: Pass all_eol_scrap_list_baseline so we get ALL alloys, not just product_list
    baseline_eol_groups = build_mix_list(product_list, "Baseline PC", all_eol_scrap_list_baseline)
    twitch_alloys = None
    for group in baseline_eol_groups:
        if group["label"] == "Twitch-PC1":
            twitch_alloys = group["alloys"]
            break
    
    if twitch_alloys is None:
        raise ValueError("Could not find Twitch-PC1 group in Baseline PC scenario")
    
    collection_rates_data_baseline = loaded_data_baseline["collection_rates_data"]
    scenario_row_baseline = loaded_data_baseline["scenario_row"]
    
    # Get EOL scrap for the specified year (same logic as in input_values function)
    all_eolscrap_generated_baseline = supply_data_baseline[year]
    collection_rates_baseline = collection_rates_data_baseline
    all_eolscrap_baseline = all_eolscrap_generated_baseline.copy()
    
    # Apply collection rates for each alloy individually
    for alloy in all_eolscrap_generated_baseline.index:
        if alloy in collection_rates_baseline.index:
            # Get the collection rate for this specific alloy and year
            collection_rate_year = float(collection_rates_baseline.loc[alloy, year])
            scrap_proc_yield = scenario_row_baseline["scrap_proc_yield"].values[0]
            
            # Multiply the EOL scrap for this alloy by its collection rate and scrap processing yield
            all_eolscrap_baseline.loc[alloy] = all_eolscrap_generated_baseline.loc[alloy] * collection_rate_year * scrap_proc_yield
        else:
            # If no collection rate is defined for this alloy, use the original quantity (effectively 100% collection rate)
            scrap_proc_yield = scenario_row_baseline["scrap_proc_yield"].values[0]
            all_eolscrap_baseline.loc[alloy] = all_eolscrap_generated_baseline.loc[alloy] * scrap_proc_yield
    
    # Calculate theoretical uncontaminated Twitch composition (weighted average using specified year quantities)
    twitch_compositions = []
    twitch_weights = []
    
    for alloy in twitch_alloys:
        # Find midrange composition for this alloy
        comp_mask = compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(alloy)
        if comp_mask.any():
            alloy_comp = compositions_data.loc[comp_mask].iloc[0]
            twitch_compositions.append(alloy_comp)
            # Use actual EOL scrap quantities from specified year as weights
            if alloy in all_eolscrap_baseline.index:
                weight = all_eolscrap_baseline[alloy]
                twitch_weights.append(weight)
            else:
                twitch_weights.append(0.0)  # If alloy not found, use zero weight
    
    if not twitch_compositions:
        raise ValueError("Could not find compositions for Twitch alloys")
    
    # Calculate weighted average composition using the same method as in mix function
    twitch_weights = np.array(twitch_weights)
    total_weight = twitch_weights.sum()
    
    if total_weight > 0:
        # Convert compositions to numpy arrays
        to_mix_compositions = np.vstack([comp.fillna(0).to_numpy() for comp in twitch_compositions])
        # Use numpy's weighted average with the specified year quantities as weights
        weights = np.broadcast_to(twitch_weights[:, None], to_mix_compositions.shape)
        theoretical_twitch_array = np.ma.average(to_mix_compositions, axis=0, weights=weights).filled(0.0)
        theoretical_twitch = pd.Series(theoretical_twitch_array, index=compositions_data.columns)
    else:
        raise ValueError(f"Total weight for Twitch alloys is zero - no EOL scrap quantities found for {year}")
    
    contamination_factors = {}
    percent_differences = []
    
    for element in target_composition:
        if element in theoretical_twitch.index:
            theoretical_value = theoretical_twitch[element]
            target_value = target_composition[element]
            
            if theoretical_value > 0:
                # Calculate percent difference: (target - theoretical) / theoretical
                percent_diff = (target_value - theoretical_value) / theoretical_value
                contamination_factors[element] = percent_diff
                percent_differences.append(abs(percent_diff))  # For averaging Cr/Ni/Ti, use absolute value
            else:
                # If theoretical is 0, use target value as contamination factor
                contamination_factors[element] = target_value
                percent_differences.append(target_value)
    
    # Calculate average for Cr, Ni, Ti
    avg_contamination = np.mean(percent_differences) if percent_differences else 0.0
    for element in ['Cr', 'Ni', 'Ti']:
        contamination_factors[element] = avg_contamination
    
    return contamination_factors

def filter_scrap_for_optimization(mixed_quantities, mixed_comps, mixed_labels, product_list_quantities, has_product_list_alloys):
    """Filter scrap streams to only include those with product_list contributions
    
    Args:
        mixed_quantities: Full quantities of each mixed stream
        mixed_comps: Compositions of each mixed stream
        mixed_labels: Labels of each mixed stream
        product_list_quantities: Quantities from product_list alloys in each stream
        has_product_list_alloys: Boolean list indicating if stream contains product_list alloys
    
    Returns:
        Tuple of (filtered_quantities, filtered_comps, filtered_labels) where:
        - Only streams that contain product_list alloys are included
        - Quantities are adjusted to only count product_list contributions
    """
    filtered_quantities = []
    filtered_comps = []
    filtered_labels = []
    
    for i, (qty, comp, label, pl_qty, has_pl) in enumerate(zip(
        mixed_quantities, mixed_comps, mixed_labels, product_list_quantities, has_product_list_alloys
    )):
        # Include streams that contain product_list alloys, even if current quantity is 0
        # This ensures alloys with 0 demand but still in product_list are considered
        if has_pl:
            filtered_quantities.append(pl_qty)  # Use product_list quantity (may be 0)
            filtered_comps.append(comp)  # Keep full composition (includes all alloys mixed)
            filtered_labels.append(label)
    
    return (
        np.array(filtered_quantities) if filtered_quantities else np.array([]),
        np.array(filtered_comps) if filtered_comps else np.array([]),
        filtered_labels
    )

def mix(to_mix, scrap_list, compositions_data, mix_fraction, product_list, scenario=None, year=None):
    """Function that mixes input streams together
    
    Args:
        to_mix: List of dictionaries defining streams to mix
        scrap_list: Series with scrap quantities indexed by alloy name
        compositions_data: DataFrame with composition data
        mix_fraction: Series with mix fractions indexed by alloy name
        product_list: List of products from demand (for tracking contributions)
        scenario: Scenario name (optional, used for Ford-specific contamination factors)
        year: Year for analysis (optional, Ford contamination factors only apply from 2030 onwards)
    
    Returns:
        Tuple of (mixed_quantities, mixed_compositions, product_list_quantities, has_product_list_alloys)
        - mixed_quantities: Total quantities of each mixed stream
        - mixed_compositions: Weighted compositions of each mixed stream
        - product_list_quantities: Quantities from product_list alloys only
        - has_product_list_alloys: Boolean list indicating if stream contains product_list alloys
    """
    mixed_quantities = []
    mixed_compositions = []
    product_list_quantities = []  # Track quantities from product_list alloys
    has_product_list_alloys = []  # Track if stream contains product_list alloys
    
    # Calculate default contamination factors dynamically based on Twitch composition
    contamination_factors_C = calculate_contamination_factors(product_list, compositions_data)
    
    # Calculate Ford-specific contamination factors
    ford_contamination_factors_C = calculate_contamination_factors(
        product_list, compositions_data, year=2024, target_composition="F-150"
    )
    
    # Check if this is a Kelly scenario with both Twitch-5xxx-PC1 and Twitch-6xxx-PC1
    kelly_contamination_factors = None
    if scenario and "Kelly" in scenario:
        # Check if both Twitch-5xxx-PC1 and Twitch-6xxx-PC1 exist in to_mix
        stream_labels = [stream.get("label", "") for stream in to_mix]
        has_5xxx = "Twitch-5xxx-PC1" in stream_labels
        has_6xxx = "Twitch-6xxx-PC1" in stream_labels
        
        # Use Kelly contamination factors if both streams are present
        if has_5xxx and has_6xxx:
            print("Kelly scenario detected with both Twitch-5xxx-PC1 and Twitch-6xxx-PC1")
            kelly_contamination_factors = calculate_kelly_contamination_factors(product_list, compositions_data, year=year if year else 2016)
    
    for streams in to_mix:
        alloys = streams["alloys"]

        # Filter to only include alloys that exist in both scrap_list and mix_fraction
        alloys = [a for a in alloys if a in scrap_list.index and a in mix_fraction.index]
        
        if not alloys:  # Skip if no valid alloys
            continue
            
        to_mix_quantities = np.array([scrap_list[alloy] * mix_fraction[alloy] for alloy in alloys])
        mixed_sum = to_mix_quantities.sum()
        mixed_quantities.append(mixed_sum)
        
        # Calculate quantity from product_list alloys only
        product_list_alloys_in_stream = [alloy for alloy in alloys if alloy in product_list]
        product_list_sum = sum([
            scrap_list[alloy] * mix_fraction[alloy] 
            for alloy in product_list_alloys_in_stream
        ])
        product_list_quantities.append(product_list_sum)
        has_product_list_alloys.append(len(product_list_alloys_in_stream) > 0)

        # Calculate mixed compositions for each mixed scrap stream
        if mixed_sum > 0:
            to_mix_compositions = np.vstack([
                compositions_data.loc[compositions_data.index.str.endswith('midrange') & compositions_data.index.str.startswith(alloy)].to_numpy() 
                for alloy in alloys
            ])

            # Turn NaNs in composition array to 0s
            arr = np.nan_to_num(to_mix_compositions, nan=0.0)
            # Stretch to_mix_quantities for element-wise multiplication
            weights = np.broadcast_to(to_mix_quantities[:, None], arr.shape)
            # Compute weighted average, iignoring masked values
            weighted_comps = np.ma.average(arr, axis=0, weights=weights).filled(np.nan)
            
            # Apply contamination factors if contamination tag exists
            if "contamination" in streams:
                
                contamination_level = streams["contamination"]
                
                if contamination_level in ["B", "C"]:
                    # Get column names from compositions_data to map elements to indices
                    element_columns = compositions_data.columns.tolist()
                    
                    # Check if this is a Kelly scenario with stream-specific factors
                    stream_label = streams.get("label", "")
                    use_kelly_factors = False
                    if kelly_contamination_factors is not None:
                        if stream_label in kelly_contamination_factors:
                            factors_to_use = kelly_contamination_factors[stream_label]
                            use_kelly_factors = True
                    
                    if not use_kelly_factors:
                        # Use Ford-specific contamination factors if:
                        # 1. This is Ford-PC1 stream with contamination C, OR
                        # 2. The scenario contains "Isolated Ford"
                        # 3. AND the year is 2030 or later
                        use_ford_factors = False
                        if year is not None and year >= 2030:
                            if scenario and "Isolated Ford" in scenario:
                                use_ford_factors = True
                        
                        if use_ford_factors:
                            factors_to_use = ford_contamination_factors_C
                        else:
                            factors_to_use = contamination_factors_C
                    
                    for element, factor_C in factors_to_use.items():
                        if element in element_columns:
                            element_idx = element_columns.index(element)
                            
                            if contamination_level == "C":
                                # Apply full contamination factor for level C
                                contamination_factor = factor_C
                            elif contamination_level == "B":
                                # Apply half contamination factor for level B
                                contamination_factor = factor_C / 2
                            
                            # Increase the composition by the contamination factor
                            # Only apply if the current composition is not NaN
                            if not np.isnan(weighted_comps[element_idx]):
                                weighted_comps[element_idx] *= (1 + contamination_factor)
                
                # For contamination level "A", no additional processing needed (original composition)
        else:
            weighted_comps = np.zeros(compositions_data.shape[1])  # Handle case where mixed sum is zero
            print("WARNING: Mixed sum is zero, using zero composition")
        
        mixed_compositions.append(weighted_comps)

    return np.array(mixed_quantities), np.array(mixed_compositions), np.array(product_list_quantities), has_product_list_alloys

def build_mix_list(product_list, mix_scenario, all_scrap_list=None):
    """Function that builds a list of mixed streams
    
    Args:
        product_list: List of products to be optimized (from demand data)
        mix_scenario: Scenario for mixing scrap streams
        all_scrap_list: Complete list of all scrap streams from supply_data (optional, defaults to product_list)
    
    Returns:
        List of dictionaries defining mixed streams, each with 'alloys' and 'label' keys
    """
    # If all_scrap_list not provided, use product_list
    if all_scrap_list is None:
        all_scrap_list = product_list
    
    # Separate list of ALL scrap streams into different sectors and shapes
    # This ensures all scrap goes through mixing, not just product_list items
    building_sheet = [a for a in all_scrap_list if 'B&C Sheet' in a]
    building_6xxx_extrusion = [a for a in all_scrap_list if 'B&C Extrusion' in a and '6xxx' in a]
    building_non6xxx_extrusion = [a for a in all_scrap_list if 'B&C Extrusion' in a and '6xxx' not in a]
    building_forgings = [a for a in all_scrap_list if 'B&C Forgings' in a]
    building_castings = [a for a in all_scrap_list if 'B&C Castings' in a]
    
    Ford_lowMg = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and 'Low Mg' in a]
    Ford_highMg = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and 'High Mg' in a]
    Ford_lowCu = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and 'Low Cu' in a]
    Ford_highCu = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and 'High Cu' in a]
    Ford_HRC_5xxx = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and '5xxx' in a and 'HRC' in a]
    Ford_HRC_6xxx = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and '6xxx' in a and 'HRC' in a]
    Ford_other_sheet = [a for a in all_scrap_list if 'Auto Sheet' in a and 'Ford' in a and 'Low Mg' not in a and 'High Mg' not in a and 'Low Cu' not in a and 'High Cu' not in a and 'HRC' not in a]
    Ford_extrusion = [a for a in all_scrap_list if 'Auto Extrusion' in a and 'Ford' in a]
    Ford_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'Ford' in a]
    Ford_nonA356_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'Ford' in a and 'A356' not in a]
    nonFord_ABS = [a for a in all_scrap_list if 'Auto Sheet' in a and ('5xxx' in a or '6xxx' in a) and 'Ford' not in a]
    nonFord_auto_nonABS_sheet = [a for a in all_scrap_list if 'Auto Sheet' in a and '5xxx' not in a and '6xxx' not in a and 'Ford' not in a]
    auto_sheet = [a for a in all_scrap_list if 'Auto Sheet' in a]
    auto_extrusion = [a for a in all_scrap_list if 'Auto Extrusion' in a]
    nonFord_auto_extrusion = [a for a in all_scrap_list if 'Auto Extrusion' in a and 'Ford' not in a]
    auto_forgings = [a for a in all_scrap_list if 'Auto Forgings' in a]
    auto_castings = [a for a in all_scrap_list if 'Auto Castings' in a]
    nonFord_auto_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'Ford' not in a]
    auto_A356_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'A356' in a]
    auto_nonA356_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'A356' not in a]
    nonFord_auto_A356_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'A356' in a and 'Ford' not in a]
    nonFord_auto_nonA356_castings = [a for a in all_scrap_list if 'Auto Castings' in a and 'A356' not in a and 'Ford' not in a]

    transport_sheet = [a for a in all_scrap_list if 'Transp. Sheet' in a]
    transport_extrusion = [a for a in all_scrap_list if 'Transp. Extrusion' in a]
    transport_forgings = [a for a in all_scrap_list if 'Transp. Forgings' in a]
    transport_castings = [a for a in all_scrap_list if 'Transp. Castings' in a]
    transport_aero = [a for a in all_scrap_list if 'Transp. ' in a and ('2xxx' in a or '7xxx' in a)]
    transport_nonaero = [a for a in all_scrap_list if 'Transp. ' in a and '2xxx' not in a and '7xxx' not in a]

    consumer_sheet = [a for a in all_scrap_list if 'Cons. Dur. Sheet' in a]
    consumer_extrusion = [a for a in all_scrap_list if 'Cons. Dur. Extrusion' in a]
    consumer_forgings = [a for a in all_scrap_list if 'Cons. Dur. Forgings' in a]
    consumer_castings = [a for a in all_scrap_list if 'Cons. Dur. Castings' in a]

    electrical_sheet = [a for a in all_scrap_list if 'Electrical Sheet' in a]
    electrical_1xxx_wire = [a for a in all_scrap_list if 'Electrical Extrusion' in a and '1xxx' in a]
    electrical_non1xxx_extrusion = [a for a in all_scrap_list if 'Electrical Extrusion' in a and '1xxx' not in a]
    electrical_forgings = [a for a in all_scrap_list if 'Electrical Forgings' in a]
    electrical_castings = [a for a in all_scrap_list if 'Electrical Castings' in a]

    machinery_sheet = [a for a in all_scrap_list if 'M&E Sheet' in a]
    machinery_extrusion = [a for a in all_scrap_list if 'M&E Extrusion' in a]
    machinery_forgings = [a for a in all_scrap_list if 'M&E Forgings' in a]
    machinery_castings = [a for a in all_scrap_list if 'M&E Castings' in a]

    container_3xxx_body_sheet = [a for a in all_scrap_list if 'Cont. & Pack. Sheet' in a and '3xxx' in a]
    container_5xxx_lid_sheet = [a for a in all_scrap_list if 'Cont. & Pack. Sheet' in a and '5xxx' in a]
    container_noncan_sheet = [a for a in all_scrap_list if 'Cont. & Pack. Sheet' in a and '3xxx' not in a and '5xxx' not in a]
    container_extrusion = [a for a in all_scrap_list if 'Cont. & Pack. Extrusion' in a]
    container_forgings = [a for a in all_scrap_list if 'Cont. & Pack. Forgings' in a]
    container_castings = [a for a in all_scrap_list if 'Cont. & Pack. Castings' in a]

    other_sheet = [a for a in all_scrap_list if 'Other Sheet' in a]
    other_extrusion = [a for a in all_scrap_list if 'Other Extrusion' in a]
    other_forgings = [a for a in all_scrap_list if 'Other Forgings' in a]
    other_castings = [a for a in all_scrap_list if 'Other Castings' in a]

    # Pre-define family-grouped variables for separation scenarios
    # BC-Other family groups
    bc_other_alloys = building_sheet + building_non6xxx_extrusion + building_forgings + building_castings
    bc_1xxx = [a for a in bc_other_alloys if '1xxx' in a]
    bc_2xxx = [a for a in bc_other_alloys if '2xxx' in a]
    bc_3xxx = [a for a in bc_other_alloys if '3xxx' in a]
    bc_4xxx = [a for a in bc_other_alloys if '4xxx' in a]
    bc_5xxx = [a for a in bc_other_alloys if '5xxx' in a]
    bc_6xxx = [a for a in bc_other_alloys if '6xxx' in a]
    bc_7xxx = [a for a in bc_other_alloys if '7xxx' in a]
    bc_8xxx = [a for a in bc_other_alloys if '8xxx' in a]
    bc_castings = [a for a in bc_other_alloys if 'Castings' in a]

    # Twitch family groups (includes Ford alloys in their respective families)
    twitch_all_alloys = (auto_sheet + auto_extrusion + auto_forgings + auto_nonA356_castings + 
                        consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings)
    twitch_1xxx = [a for a in twitch_all_alloys if '1xxx' in a]
    twitch_2xxx = [a for a in twitch_all_alloys if '2xxx' in a]
    twitch_3xxx = [a for a in twitch_all_alloys if '3xxx' in a]
    twitch_4xxx = [a for a in twitch_all_alloys if '4xxx' in a]
    twitch_5xxx = [a for a in twitch_all_alloys if '5xxx' in a] + Ford_lowMg + Ford_highMg
    twitch_6xxx = [a for a in twitch_all_alloys if '6xxx' in a] + Ford_lowCu + Ford_highCu
    twitch_7xxx = [a for a in twitch_all_alloys if '7xxx' in a]
    twitch_8xxx = [a for a in twitch_all_alloys if '8xxx' in a]
    twitch_castings = [a for a in twitch_all_alloys if 'Castings' in a]

    # Transport aero family groups
    transport_aero_1xxx = [a for a in transport_aero if '1xxx' in a]
    transport_aero_2xxx = [a for a in transport_aero if '2xxx' in a]
    transport_aero_3xxx = [a for a in transport_aero if '3xxx' in a]
    transport_aero_4xxx = [a for a in transport_aero if '4xxx' in a]
    transport_aero_5xxx = [a for a in transport_aero if '5xxx' in a]
    transport_aero_6xxx = [a for a in transport_aero if '6xxx' in a]
    transport_aero_7xxx = [a for a in transport_aero if '7xxx' in a]
    transport_aero_8xxx = [a for a in transport_aero if '8xxx' in a]
    transport_aero_castings = [a for a in transport_aero if 'Castings' in a]

    # Transport non-aero family groups
    transport_nonaero_1xxx = [a for a in transport_nonaero if '1xxx' in a]
    transport_nonaero_2xxx = [a for a in transport_nonaero if '2xxx' in a]
    transport_nonaero_3xxx = [a for a in transport_nonaero if '3xxx' in a]
    transport_nonaero_4xxx = [a for a in transport_nonaero if '4xxx' in a]
    transport_nonaero_5xxx = [a for a in transport_nonaero if '5xxx' in a]
    transport_nonaero_6xxx = [a for a in transport_nonaero if '6xxx' in a]
    transport_nonaero_7xxx = [a for a in transport_nonaero if '7xxx' in a]
    transport_nonaero_8xxx = [a for a in transport_nonaero if '8xxx' in a]
    transport_nonaero_castings = [a for a in transport_nonaero if 'Castings' in a]

    # Electrical non-1xxx family groups
    electrical_non1xxx_extrusion_all = electrical_non1xxx_extrusion
    electrical_nonextrusions_all = electrical_sheet + electrical_forgings + electrical_castings
    electrical_1xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '1xxx' in a]
    electrical_2xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '2xxx' in a]
    electrical_3xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '3xxx' in a]
    electrical_4xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '4xxx' in a]
    electrical_5xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '5xxx' in a]
    electrical_6xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '6xxx' in a]
    electrical_7xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '7xxx' in a]
    electrical_8xxx_extrusions = [a for a in electrical_non1xxx_extrusion_all if '8xxx' in a]
    electrical_1xxx_other = [a for a in electrical_nonextrusions_all if '1xxx' in a]
    electrical_2xxx_other = [a for a in electrical_nonextrusions_all if '2xxx' in a]
    electrical_3xxx_other = [a for a in electrical_nonextrusions_all if '3xxx' in a]
    electrical_4xxx_other = [a for a in electrical_nonextrusions_all if '4xxx' in a]
    electrical_5xxx_other = [a for a in electrical_nonextrusions_all if '5xxx' in a]
    electrical_6xxx_other = [a for a in electrical_nonextrusions_all if '6xxx' in a]
    electrical_7xxx_other = [a for a in electrical_nonextrusions_all if '7xxx' in a]
    electrical_8xxx_other = [a for a in electrical_nonextrusions_all if '8xxx' in a]
    electrical_castings_grp = [a for a in electrical_nonextrusions_all if 'Castings' in a]


    # Machinery family groups
    machinery_all = machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings
    machinery_1xxx = [a for a in machinery_all if '1xxx' in a]
    machinery_2xxx = [a for a in machinery_all if '2xxx' in a]
    machinery_3xxx = [a for a in machinery_all if '3xxx' in a]
    machinery_4xxx = [a for a in machinery_all if '4xxx' in a]
    machinery_5xxx = [a for a in machinery_all if '5xxx' in a]
    machinery_6xxx = [a for a in machinery_all if '6xxx' in a]
    machinery_7xxx = [a for a in machinery_all if '7xxx' in a]
    machinery_8xxx = [a for a in machinery_all if '8xxx' in a]
    machinery_castings_grp = [a for a in machinery_all if 'Castings' in a]

    # Container other family groups
    container_other_all = container_noncan_sheet + container_extrusion + container_forgings + container_castings
    container_1xxx = [a for a in container_other_all if '1xxx' in a]
    container_2xxx = [a for a in container_other_all if '2xxx' in a]
    container_3xxx_other = [a for a in container_other_all if '3xxx' in a]
    container_4xxx = [a for a in container_other_all if '4xxx' in a]
    container_5xxx_other = [a for a in container_other_all if '5xxx' in a]
    container_6xxx = [a for a in container_other_all if '6xxx' in a]
    container_7xxx = [a for a in container_other_all if '7xxx' in a]
    container_8xxx = [a for a in container_other_all if '8xxx' in a]
    container_castings_grp = [a for a in container_other_all if 'Castings' in a]

    # Other family groups
    other_all = other_sheet + other_extrusion + other_forgings + other_castings
    other_1xxx = [a for a in other_all if '1xxx' in a]
    other_2xxx = [a for a in other_all if '2xxx' in a]
    other_3xxx = [a for a in other_all if '3xxx' in a]
    other_4xxx = [a for a in other_all if '4xxx' in a]
    other_5xxx = [a for a in other_all if '5xxx' in a]
    other_6xxx = [a for a in other_all if '6xxx' in a]
    other_7xxx = [a for a in other_all if '7xxx' in a]
    other_8xxx = [a for a in other_all if '8xxx' in a]
    other_castings_grp = [a for a in other_all if 'Castings' in a]

    # Build list of mixed streams based on mixing scenario
    # This block is activated for forming scrap mixing scenarios
    # As more forming scrap mixing scenarios are added, add definitions here
    if mix_scenario == "Baseline forming":
        to_mix = []
    else:
        pass

    # This block is activated for fabrication scrap mixing scenarios
    # As more fabrication scrap mixing scenarios are added, add definitions here
    if mix_scenario == "Baseline fabrication":
        to_mix = []
        # Each entry in to_mix is a dict that contains streams to be mixed and their associated labels
        streams = [
            {
                "alloys": building_6xxx_extrusion + building_sheet + building_non6xxx_extrusion + building_forgings + building_castings,
                "label": "BC-Fab1"
            },
            {
                "alloys": Ford_lowMg,
                "label": "T-LDV-Fab-LowMg"
            },
            {
                "alloys": Ford_highMg,
                "label": "T-LDV-Fab-HighMg"
            },
            {
                "alloys": Ford_lowCu,
                "label": "T-LDV-Fab-LowCu"
            },
            {
                "alloys": Ford_highCu,
                "label": "T-LDV-Fab-HighCu"
            },
            {
                "alloys": Ford_HRC_5xxx,
                "label": "T-LDV-Fab-FordHRC-5xxx"
            },
            {
                "alloys": Ford_HRC_6xxx,
                "label": "T-LDV-Fab-FordHRC-6xxx"
            },
            {
                "alloys": nonFord_ABS,
                "label": "T-LDV-Fab1"
            },
            {
                "alloys": nonFord_auto_nonABS_sheet,
                "label": "T-LDV-Fab2"
            },
            {
                "alloys": auto_extrusion,
                "label": "T-LDV-Fab3"
            },
            {
                "alloys": auto_forgings,
                "label": "T-LDV-Fab4"
            },
            {
                "alloys": auto_castings,
                "label": "T-LDV-Fab5"
            },
            {
                "alloys": transport_sheet,
                "label": "T-Other-Fab1"
            },
            {
                "alloys": transport_extrusion,
                "label": "T-Other-Fab2"
            },
            {
                "alloys": transport_forgings,
                "label": "T-Other-Fab3"
            },
            {
                "alloys": transport_castings,
                "label": "T-Other-Fab4"
            },
            {
                "alloys": consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings,
                "label": "CD-Fab1"
            },
            {
                "alloys": electrical_1xxx_wire,
                "label": "E-Fab1"
            },
            {
                "alloys": electrical_non1xxx_extrusion,
                "label": "E-Fab2"
            },
            {
                "alloys": electrical_sheet + electrical_forgings + electrical_castings,
                "label": "E-Fab3"
            },
            {
                "alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings,
                "label": "ME-Fab1"
            },
            {
                "alloys": container_3xxx_body_sheet,
                "label": "CP-Can-Fab1"
            },
            {
                "alloys": container_5xxx_lid_sheet,
                "label": "CP-Can-Fab2"
            },
            {
                "alloys": container_noncan_sheet,
                "label": "CP-Foil-Fab1"
            },
            {
                "alloys": container_extrusion + container_forgings + container_castings,
                "label": "CP-Other-Fab1"
            },
            {
                "alloys": other_sheet + other_extrusion + other_forgings + other_castings,
                "label": "O-Fab1"
            }
        ]
        # Filter out empty streams
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Separate fabrication":
        to_mix = []
    else:
        pass

    # This block is activated for EOL scrap mixing scenarios
    # Only support the 10 allowed scenarios for eol_mix_scenario
    if mix_scenario == "Baseline PC":
        # Each entry in to_mix is a dict that contains streams to be mixed, their associated labels, and their level of contamination
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "B"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "C"},
            {"alloys": auto_sheet + auto_extrusion + auto_forgings + auto_nonA356_castings + consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings, "label": "Twitch-PC1", "contamination": "C"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "C"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "C"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "B"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "C"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "C"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "C"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "C"}
        ]
        # Filter out empty streams
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "No PC contamination":
        # Same mixing as baseline, but all contamination is "A"
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "A"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "A"},
            {"alloys": auto_sheet + auto_extrusion + auto_forgings + auto_nonA356_castings + consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings, "label": "Twitch-PC1", "contamination": "A"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "A"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "A"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "A"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "A"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "A"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "A"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "A"}
        ]
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Separate consumer durables":
        # Each entry in to_mix is a dict that contains streams to be mixed, their associated labels, and their level of contamination
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "B"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "C"},
            {"alloys": auto_sheet + auto_extrusion + auto_forgings + auto_nonA356_castings, "label": "Twitch-PC1", "contamination": "C"},
            {"alloys": consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings, "label": "Consumer-Durables-PC1", "contamination": "C"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "C"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "C"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "B"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "C"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "C"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "C"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "C"}
        ]
        # Filter out empty streams
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Separate consumer durables without contamination":
        # Same mixing as baseline, but all contamination is "A"
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "A"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "A"},
            {"alloys": auto_sheet + auto_extrusion + auto_forgings + auto_nonA356_castings, "label": "Twitch-PC1", "contamination": "A"},
            {"alloys": consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings, "label": "Consumer-Durables-PC1", "contamination": "A"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "A"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "A"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "A"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "A"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "A"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "A"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "A"}
        ]
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Isolated Ford":
        # Each entry in to_mix is a dict that contains streams to be mixed, their associated labels, and their level of contamination
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "B"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "C"},
            {"alloys": Ford_lowMg + Ford_highMg + Ford_lowCu + Ford_highCu + Ford_other_sheet + Ford_extrusion + Ford_nonA356_castings, "label": "Ford-PC1", "contamination": "C"},
            {"alloys": nonFord_ABS + nonFord_auto_nonABS_sheet + nonFord_auto_extrusion + auto_forgings + nonFord_auto_nonA356_castings + consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings, "label": "Twitch-PC1", "contamination": "C"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "C"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "C"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "B"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "C"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "C"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "C"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "C"}
        ]
        # Filter out empty streams
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Isolated Ford no contamination":
        # Same mixing as baseline, but all contamination is "A"
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "A"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "A"},
            {"alloys": Ford_lowMg + Ford_highMg + Ford_lowCu + Ford_highCu + Ford_other_sheet + Ford_extrusion + Ford_nonA356_castings, "label": "Ford-PC1", "contamination": "A"},
            {"alloys": nonFord_ABS + nonFord_auto_nonABS_sheet + nonFord_auto_extrusion + auto_forgings + nonFord_auto_nonA356_castings + consumer_sheet + consumer_extrusion + consumer_forgings + consumer_castings, "label": "Twitch-PC1", "contamination": "A"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "A"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "A"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "A"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "A"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "A"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "A"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "A"}
        ]
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Auto castings separated":
        # Each entry in to_mix is a dict that contains streams to be mixed, their associated labels, and their level of contamination
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "B"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "C"},
            {"alloys": auto_sheet + auto_extrusion + auto_forgings + consumer_sheet + consumer_extrusion + consumer_forgings, "label": "Twitch-PC1", "contamination": "C"},
            {"alloys": auto_nonA356_castings + consumer_castings, "label": "Twitch-NonA356-Castings-PC1", "contamination": "C"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "C"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "C"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "B"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "C"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "C"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "C"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "C"}
        ]
        # Filter out empty streams
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "Auto castings separated and no contamination":
        # Each entry in to_mix is a dict that contains streams to be mixed, their associated labels, and their level of contamination
        streams = [
            {"alloys": building_6xxx_extrusion, "label": "BC-6xxx-Extrusions-PC1", "contamination": "B"},
            {"alloys": building_sheet + building_non6xxx_extrusion + building_forgings + building_castings, "label": "BC-Other-PC1", "contamination": "C"},
            {"alloys": auto_sheet + auto_extrusion + auto_forgings + consumer_sheet + consumer_extrusion + consumer_forgings, "label": "Twitch-PC1", "contamination": "A"},
            {"alloys": auto_nonA356_castings + consumer_castings, "label": "Twitch-NonA356-Castings-PC1", "contamination": "A"},
            {"alloys": auto_A356_castings, "label": "LDV-A356-Castings-PC1", "contamination": "A"},
            {"alloys": transport_aero, "label": "T-Other-PC1", "contamination": "C"},
            {"alloys": transport_nonaero, "label": "T-Other-PC2", "contamination": "C"},
            {"alloys": electrical_1xxx_wire, "label": "E-PC1", "contamination": "A"},
            {"alloys": electrical_non1xxx_extrusion, "label": "E-PC2", "contamination": "B"},
            {"alloys": electrical_sheet + electrical_forgings + electrical_castings, "label": "E-PC3", "contamination": "C"},
            {"alloys": machinery_sheet + machinery_extrusion + machinery_forgings + machinery_castings, "label": "ME-PC1", "contamination": "C"},
            {"alloys": container_3xxx_body_sheet + container_5xxx_lid_sheet, "label": "UBC-PC1", "contamination": "A"},
            {"alloys": container_noncan_sheet + container_extrusion + container_forgings + container_castings, "label": "CP-Other-PC1", "contamination": "C"},
            {"alloys": other_sheet + other_extrusion + other_forgings + other_castings, "label": "Other-PC1", "contamination": "C"}
        ]
        # Filter out empty streams
        to_mix = [stream for stream in streams if stream["alloys"]]
    elif mix_scenario == "93% accuracy PC scrap separation by family" or mix_scenario == "100% accuracy PC scrap separation by family":
        # Create grouped streams with alloy family separation within each baseline group
        to_mix = []
        
        # BC-6xxx-Extrusions group - only 6xxx extrusions (no change from baseline)
        if building_6xxx_extrusion:
            to_mix.append({
                "alloys": building_6xxx_extrusion,
                "label": "BC-6xxx-Extrusions-PC1",
                "contamination": "B",
                "separation_group": "BC-6xxx-Extrusions"
            })
        
        # BC-Other group - separate by alloy family
        for family_name, alloys in [
            ("BC-1xxx-PC1", bc_1xxx),
            ("BC-2xxx-PC1", bc_2xxx),
            ("BC-3xxx-PC1", bc_3xxx),
            ("BC-4xxx-PC1", bc_4xxx),
            ("BC-5xxx-PC1", bc_5xxx),
            ("BC-6xxx-PC1", bc_6xxx),
            ("BC-7xxx-PC1", bc_7xxx),
            ("BC-8xxx-PC1", bc_8xxx),
            ("BC-Castings-PC1", bc_castings)
        ]:
            if alloys:
                to_mix.append({
                    "alloys": alloys,
                    "label": family_name,
                    "contamination": "C",
                    "separation_group": "BC-Other"
                })
        
        # Twitch group - separate by alloy family (with Ford handling)
        for family_name, alloys in [
            ("Twitch-1xxx-PC1", twitch_1xxx),
            ("Twitch-2xxx-PC1", twitch_2xxx),
            ("Twitch-3xxx-PC1", twitch_3xxx),
            ("Twitch-4xxx-PC1", twitch_4xxx),
            ("Twitch-5xxx-PC1", twitch_5xxx),
            ("Twitch-6xxx-PC1", twitch_6xxx),
            ("Twitch-7xxx-PC1", twitch_7xxx),
            ("Twitch-8xxx-PC1", twitch_8xxx),
            ("Twitch-Castings-PC1", twitch_castings)
        ]:
            if alloys:
                to_mix.append({
                    "alloys": alloys,
                    "label": family_name,
                    "contamination": "C",
                    "separation_group": "Twitch"
                })
        
        # A356 Castings group (unchanged)
        if auto_A356_castings:
            to_mix.append({
                "alloys": auto_A356_castings,
                "label": "LDV-A356-Castings-PC1",
                "contamination": "A",
                "separation_group": "LDV-A356-Castings"
            })
        
        # Transport aero group - separate by alloy family
        if transport_aero:
            for family_name, alloys in [
                ("T-Aero-1xxx-PC1", transport_aero_1xxx),
                ("T-Aero-2xxx-PC1", transport_aero_2xxx),
                ("T-Aero-3xxx-PC1", transport_aero_3xxx),
                ("T-Aero-4xxx-PC1", transport_aero_4xxx),
                ("T-Aero-5xxx-PC1", transport_aero_5xxx),
                ("T-Aero-6xxx-PC1", transport_aero_6xxx),
                ("T-Aero-7xxx-PC1", transport_aero_7xxx),
                ("T-Aero-8xxx-PC1", transport_aero_8xxx),
                ("T-Aero-Castings-PC1", transport_aero_castings)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "C",
                        "separation_group": "T-Aero"
                    })
        
        # Transport non-aero group - separate by alloy family
        if transport_nonaero:
            for family_name, alloys in [
                ("T-Other-1xxx-PC1", transport_nonaero_1xxx),
                ("T-Other-2xxx-PC1", transport_nonaero_2xxx),
                ("T-Other-3xxx-PC1", transport_nonaero_3xxx),
                ("T-Other-4xxx-PC1", transport_nonaero_4xxx),
                ("T-Other-5xxx-PC1", transport_nonaero_5xxx),
                ("T-Other-6xxx-PC1", transport_nonaero_6xxx),
                ("T-Other-7xxx-PC1", transport_nonaero_7xxx),
                ("T-Other-8xxx-PC1", transport_nonaero_8xxx),
                ("T-Other-Castings-PC1", transport_nonaero_castings)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "C",
                        "separation_group": "T-Other"
                    })
        
        # Electrical groups - separate by alloy family
        if electrical_1xxx_wire:
            to_mix.append({
                "alloys": electrical_1xxx_wire,
                "label": "E-1xxx-wire-PC1",
                "contamination": "A",
                "separation_group": "E-1xxx"
            })
        
        # Group non-1xxx electrical extrusions by family
        if electrical_non1xxx_extrusion_all:
            for family_name, alloys in [
                ("E-1xxx-extrusions-PC1", electrical_1xxx_extrusions),
                ("E-2xxx-extrusions-PC1", electrical_2xxx_extrusions),
                ("E-3xxx-extrusions-PC1", electrical_3xxx_extrusions),
                ("E-4xxx-extrusions-PC1", electrical_4xxx_extrusions),
                ("E-5xxx-extrusions-PC1", electrical_5xxx_extrusions),
                ("E-6xxx-extrusions-PC1", electrical_6xxx_extrusions),
                ("E-7xxx-extrusions-PC1", electrical_7xxx_extrusions),
                ("E-8xxx-extrusions-PC1", electrical_8xxx_extrusions)
            ]:
                if alloys:
                    contamination = "B"
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": contamination,
                        "separation_group": "E-Non1xxx-extrusions"
                    })

        # Group electrical non-extrusions by family
        if electrical_nonextrusions_all:
            for family_name, alloys in [
                ("E-1xxx-PC1", electrical_1xxx_other),
                ("E-2xxx-PC1", electrical_2xxx_other),
                ("E-3xxx-PC1", electrical_3xxx_other),
                ("E-4xxx-PC1", electrical_4xxx_other),
                ("E-5xxx-PC1", electrical_5xxx_other),
                ("E-6xxx-PC1", electrical_6xxx_other),
                ("E-7xxx-PC1", electrical_7xxx_other),
                ("E-8xxx-PC1", electrical_8xxx_other),
                ("E-Castings-PC1", electrical_castings_grp)
            ]:
                if alloys:
                    contamination = "C"
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": contamination,
                        "separation_group": "E-Other"
                    })

        # Machinery group - separate by alloy family
        if machinery_all:
            for family_name, alloys in [
                ("ME-1xxx-PC1", machinery_1xxx),
                ("ME-2xxx-PC1", machinery_2xxx),
                ("ME-3xxx-PC1", machinery_3xxx),
                ("ME-4xxx-PC1", machinery_4xxx),
                ("ME-5xxx-PC1", machinery_5xxx),
                ("ME-6xxx-PC1", machinery_6xxx),
                ("ME-7xxx-PC1", machinery_7xxx),
                ("ME-8xxx-PC1", machinery_8xxx),
                ("ME-Castings-PC1", machinery_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "C",
                        "separation_group": "ME"
                    })
        
        # UBC group - separate by alloy family (only 3xxx and 5xxx)
        if container_3xxx_body_sheet:
            to_mix.append({
                "alloys": container_3xxx_body_sheet,
                "label": "UBC-3xxx-PC1",
                "contamination": "A",
                "separation_group": "UBC"
            })
        if container_5xxx_lid_sheet:
            to_mix.append({
                "alloys": container_5xxx_lid_sheet,
                "label": "UBC-5xxx-PC1",
                "contamination": "A",
                "separation_group": "UBC"
            })
        
        # Container other group - separate by alloy family
        if container_other_all:
            for family_name, alloys in [
                ("CP-Other-1xxx-PC1", container_1xxx),
                ("CP-Other-2xxx-PC1", container_2xxx),
                ("CP-Other-3xxx-PC1", container_3xxx_other),
                ("CP-Other-4xxx-PC1", container_4xxx),
                ("CP-Other-5xxx-PC1", container_5xxx_other),
                ("CP-Other-6xxx-PC1", container_6xxx),
                ("CP-Other-7xxx-PC1", container_7xxx),
                ("CP-Other-8xxx-PC1", container_8xxx),
                ("CP-Other-Castings-PC1", container_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "C",
                        "separation_group": "CP-Other"
                    })
        
        # Other group - separate by alloy family
        if other_all:
            for family_name, alloys in [
                ("Other-1xxx-PC1", other_1xxx),
                ("Other-2xxx-PC1", other_2xxx),
                ("Other-3xxx-PC1", other_3xxx),
                ("Other-4xxx-PC1", other_4xxx),
                ("Other-5xxx-PC1", other_5xxx),
                ("Other-6xxx-PC1", other_6xxx),
                ("Other-7xxx-PC1", other_7xxx),
                ("Other-8xxx-PC1", other_8xxx),
                ("Other-Castings-PC1", other_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "C",
                        "separation_group": "Other"
                    })
        
        # Filter out empty streams
        to_mix = [stream for stream in to_mix if stream["alloys"]]
    elif mix_scenario == "93% accuracy PC scrap separation by family and no contamination" or mix_scenario == "100% accuracy PC scrap separation by family and no contamination" or mix_scenario == "88% accuracy PC scrap separation by family and no contamination":
        # Create grouped streams with alloy family separation within each baseline group
        to_mix = []
        
        # BC-6xxx-Extrusions group - only 6xxx extrusions (no change from baseline)
        if building_6xxx_extrusion:
            to_mix.append({
                "alloys": building_6xxx_extrusion,
                "label": "BC-6xxx-Extrusions-PC1",
                "contamination": "A",
                "separation_group": "BC-6xxx-Extrusions"
            })
        
        # BC-Other group - separate by alloy family
        for family_name, alloys in [
            ("BC-1xxx-PC1", bc_1xxx),
            ("BC-2xxx-PC1", bc_2xxx),
            ("BC-3xxx-PC1", bc_3xxx),
            ("BC-4xxx-PC1", bc_4xxx),
            ("BC-5xxx-PC1", bc_5xxx),
            ("BC-6xxx-PC1", bc_6xxx),
            ("BC-7xxx-PC1", bc_7xxx),
            ("BC-8xxx-PC1", bc_8xxx),
            ("BC-Castings-PC1", bc_castings)
        ]:
            if alloys:
                to_mix.append({
                    "alloys": alloys,
                    "label": family_name,
                    "contamination": "A",
                    "separation_group": "BC-Other"
                })
        
        # Twitch group - separate by alloy family (with Ford handling)
        for family_name, alloys in [
            ("Twitch-1xxx-PC1", twitch_1xxx),
            ("Twitch-2xxx-PC1", twitch_2xxx),
            ("Twitch-3xxx-PC1", twitch_3xxx),
            ("Twitch-4xxx-PC1", twitch_4xxx),
            ("Twitch-5xxx-PC1", twitch_5xxx),
            ("Twitch-6xxx-PC1", twitch_6xxx),
            ("Twitch-7xxx-PC1", twitch_7xxx),
            ("Twitch-8xxx-PC1", twitch_8xxx),
            ("Twitch-Castings-PC1", twitch_castings)
        ]:
            if alloys:
                to_mix.append({
                    "alloys": alloys,
                    "label": family_name,
                    "contamination": "A",
                    "separation_group": "Twitch"
                })
        
        # A356 Castings group (unchanged)
        if auto_A356_castings:
            to_mix.append({
                "alloys": auto_A356_castings,
                "label": "LDV-A356-Castings-PC1",
                "contamination": "A",
                "separation_group": "LDV-A356-Castings"
            })
        
        # Transport aero group - separate by alloy family
        if transport_aero:
            for family_name, alloys in [
                ("T-Aero-1xxx-PC1", transport_aero_1xxx),
                ("T-Aero-2xxx-PC1", transport_aero_2xxx),
                ("T-Aero-3xxx-PC1", transport_aero_3xxx),
                ("T-Aero-4xxx-PC1", transport_aero_4xxx),
                ("T-Aero-5xxx-PC1", transport_aero_5xxx),
                ("T-Aero-6xxx-PC1", transport_aero_6xxx),
                ("T-Aero-7xxx-PC1", transport_aero_7xxx),
                ("T-Aero-8xxx-PC1", transport_aero_8xxx),
                ("T-Aero-Castings-PC1", transport_aero_castings)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "T-Aero"
                    })
        
        # Transport non-aero group - separate by alloy family
        if transport_nonaero:
            for family_name, alloys in [
                ("T-Other-1xxx-PC1", transport_nonaero_1xxx),
                ("T-Other-2xxx-PC1", transport_nonaero_2xxx),
                ("T-Other-3xxx-PC1", transport_nonaero_3xxx),
                ("T-Other-4xxx-PC1", transport_nonaero_4xxx),
                ("T-Other-5xxx-PC1", transport_nonaero_5xxx),
                ("T-Other-6xxx-PC1", transport_nonaero_6xxx),
                ("T-Other-7xxx-PC1", transport_nonaero_7xxx),
                ("T-Other-8xxx-PC1", transport_nonaero_8xxx),
                ("T-Other-Castings-PC1", transport_nonaero_castings)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "T-Other"
                    })
        
        # Electrical groups - separate by alloy family
        if electrical_1xxx_wire:
            to_mix.append({
                "alloys": electrical_1xxx_wire,
                "label": "E-1xxx-wire-PC1",
                "contamination": "A",
                "separation_group": "E-1xxx"
            })
        
        # Group non-1xxx electrical extrusions by family
        if electrical_non1xxx_extrusion_all:
            for family_name, alloys in [
                ("E-1xxx-extrusions-PC1", electrical_1xxx_extrusions),
                ("E-2xxx-extrusions-PC1", electrical_2xxx_extrusions),
                ("E-3xxx-extrusions-PC1", electrical_3xxx_extrusions),
                ("E-4xxx-extrusions-PC1", electrical_4xxx_extrusions),
                ("E-5xxx-extrusions-PC1", electrical_5xxx_extrusions),
                ("E-6xxx-extrusions-PC1", electrical_6xxx_extrusions),
                ("E-7xxx-extrusions-PC1", electrical_7xxx_extrusions),
                ("E-8xxx-extrusions-PC1", electrical_8xxx_extrusions)
            ]:
                if alloys:
                    contamination = "A"
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": contamination,
                        "separation_group": "E-Non1xxx-extrusions"
                    })

        # Group electrical non-extrusions by family
        if electrical_nonextrusions_all:
            for family_name, alloys in [
                ("E-1xxx-PC1", electrical_1xxx_other),
                ("E-2xxx-PC1", electrical_2xxx_other),
                ("E-3xxx-PC1", electrical_3xxx_other),
                ("E-4xxx-PC1", electrical_4xxx_other),
                ("E-5xxx-PC1", electrical_5xxx_other),
                ("E-6xxx-PC1", electrical_6xxx_other),
                ("E-7xxx-PC1", electrical_7xxx_other),
                ("E-8xxx-PC1", electrical_8xxx_other),
                ("E-Castings-PC1", electrical_castings_grp)
            ]:
                if alloys:
                    contamination = "A"
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": contamination,
                        "separation_group": "E-Other"
                    })
        
        # Machinery group - separate by alloy family
        if machinery_all:
            for family_name, alloys in [
                ("ME-1xxx-PC1", machinery_1xxx),
                ("ME-2xxx-PC1", machinery_2xxx),
                ("ME-3xxx-PC1", machinery_3xxx),
                ("ME-4xxx-PC1", machinery_4xxx),
                ("ME-5xxx-PC1", machinery_5xxx),
                ("ME-6xxx-PC1", machinery_6xxx),
                ("ME-7xxx-PC1", machinery_7xxx),
                ("ME-8xxx-PC1", machinery_8xxx),
                ("ME-Castings-PC1", machinery_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "ME"
                    })
        
        # UBC group - separate by alloy family (only 3xxx and 5xxx)
        if container_3xxx_body_sheet:
            to_mix.append({
                "alloys": container_3xxx_body_sheet,
                "label": "UBC-3xxx-PC1",
                "contamination": "A",
                "separation_group": "UBC"
            })
        if container_5xxx_lid_sheet:
            to_mix.append({
                "alloys": container_5xxx_lid_sheet,
                "label": "UBC-5xxx-PC1",
                "contamination": "A",
                "separation_group": "UBC"
            })
        
        # Container other group - separate by alloy family
        if container_other_all:
            for family_name, alloys in [
                ("CP-Other-1xxx-PC1", container_1xxx),
                ("CP-Other-2xxx-PC1", container_2xxx),
                ("CP-Other-3xxx-PC1", container_3xxx_other),
                ("CP-Other-4xxx-PC1", container_4xxx),
                ("CP-Other-5xxx-PC1", container_5xxx_other),
                ("CP-Other-6xxx-PC1", container_6xxx),
                ("CP-Other-7xxx-PC1", container_7xxx),
                ("CP-Other-8xxx-PC1", container_8xxx),
                ("CP-Other-Castings-PC1", container_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "CP-Other"
                    })
        
        # Other group - separate by alloy family
        if other_all:
            for family_name, alloys in [
                ("Other-1xxx-PC1", other_1xxx),
                ("Other-2xxx-PC1", other_2xxx),
                ("Other-3xxx-PC1", other_3xxx),
                ("Other-4xxx-PC1", other_4xxx),
                ("Other-5xxx-PC1", other_5xxx),
                ("Other-6xxx-PC1", other_6xxx),
                ("Other-7xxx-PC1", other_7xxx),
                ("Other-8xxx-PC1", other_8xxx),
                ("Other-Castings-PC1", other_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "Other"
                    })
        
        # Filter out empty streams
        to_mix = [stream for stream in to_mix if stream["alloys"]]
    elif mix_scenario == "93% accuracy PC scrap separation by family and mid contamination" or mix_scenario == "100% accuracy PC scrap separation by family and mid contamination":
        # Create grouped streams with alloy family separation within each baseline group
        to_mix = []
        
        # BC-6xxx-Extrusions group - only 6xxx extrusions (no change from baseline)
        if building_6xxx_extrusion:
            to_mix.append({
                "alloys": building_6xxx_extrusion,
                "label": "BC-6xxx-Extrusions-PC1",
                "contamination": "A",
                "separation_group": "BC-6xxx-Extrusions"
            })
        
        # BC-Other group - separate by alloy family
        for family_name, alloys in [
            ("BC-1xxx-PC1", bc_1xxx),
            ("BC-2xxx-PC1", bc_2xxx),
            ("BC-3xxx-PC1", bc_3xxx),
            ("BC-4xxx-PC1", bc_4xxx),
            ("BC-5xxx-PC1", bc_5xxx),
            ("BC-6xxx-PC1", bc_6xxx),
            ("BC-7xxx-PC1", bc_7xxx),
            ("BC-8xxx-PC1", bc_8xxx),
            ("BC-Castings-PC1", bc_castings)
        ]:
            if alloys:
                to_mix.append({
                    "alloys": alloys,
                    "label": family_name,
                    "contamination": "A",
                    "separation_group": "BC-Other"
                })
        
        # Twitch group - separate by alloy family (with Ford handling)
        for family_name, alloys in [
            ("Twitch-1xxx-PC1", twitch_1xxx),
            ("Twitch-2xxx-PC1", twitch_2xxx),
            ("Twitch-3xxx-PC1", twitch_3xxx),
            ("Twitch-4xxx-PC1", twitch_4xxx),
            ("Twitch-5xxx-PC1", twitch_5xxx),
            ("Twitch-6xxx-PC1", twitch_6xxx),
            ("Twitch-7xxx-PC1", twitch_7xxx),
            ("Twitch-8xxx-PC1", twitch_8xxx),
            ("Twitch-Castings-PC1", twitch_castings)
        ]:
            if alloys:
                to_mix.append({
                    "alloys": alloys,
                    "label": family_name,
                    "contamination": "B",
                    "separation_group": "Twitch"
                })
        
        # A356 Castings group (unchanged)
        if auto_A356_castings:
            to_mix.append({
                "alloys": auto_A356_castings,
                "label": "LDV-A356-Castings-PC1",
                "contamination": "A",
                "separation_group": "LDV-A356-Castings"
            })
        
        # Transport aero group - separate by alloy family
        if transport_aero:
            for family_name, alloys in [
                ("T-Aero-1xxx-PC1", transport_aero_1xxx),
                ("T-Aero-2xxx-PC1", transport_aero_2xxx),
                ("T-Aero-3xxx-PC1", transport_aero_3xxx),
                ("T-Aero-4xxx-PC1", transport_aero_4xxx),
                ("T-Aero-5xxx-PC1", transport_aero_5xxx),
                ("T-Aero-6xxx-PC1", transport_aero_6xxx),
                ("T-Aero-7xxx-PC1", transport_aero_7xxx),
                ("T-Aero-8xxx-PC1", transport_aero_8xxx),
                ("T-Aero-Castings-PC1", transport_aero_castings)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "T-Aero"
                    })
        
        # Transport non-aero group - separate by alloy family
        if transport_nonaero:
            for family_name, alloys in [
                ("T-Other-1xxx-PC1", transport_nonaero_1xxx),
                ("T-Other-2xxx-PC1", transport_nonaero_2xxx),
                ("T-Other-3xxx-PC1", transport_nonaero_3xxx),
                ("T-Other-4xxx-PC1", transport_nonaero_4xxx),
                ("T-Other-5xxx-PC1", transport_nonaero_5xxx),
                ("T-Other-6xxx-PC1", transport_nonaero_6xxx),
                ("T-Other-7xxx-PC1", transport_nonaero_7xxx),
                ("T-Other-8xxx-PC1", transport_nonaero_8xxx),
                ("T-Other-Castings-PC1", transport_nonaero_castings)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "T-Other"
                    })
        
        # Electrical groups - separate by alloy family
        if electrical_1xxx_wire:
            to_mix.append({
                "alloys": electrical_1xxx_wire,
                "label": "E-1xxx-wire-PC1",
                "contamination": "A",
                "separation_group": "E-1xxx"
            })
        
        # Group non-1xxx electrical extrusions by family
        if electrical_non1xxx_extrusion_all:
            for family_name, alloys in [
                ("E-1xxx-extrusions-PC1", electrical_1xxx_extrusions),
                ("E-2xxx-extrusions-PC1", electrical_2xxx_extrusions),
                ("E-3xxx-extrusions-PC1", electrical_3xxx_extrusions),
                ("E-4xxx-extrusions-PC1", electrical_4xxx_extrusions),
                ("E-5xxx-extrusions-PC1", electrical_5xxx_extrusions),
                ("E-6xxx-extrusions-PC1", electrical_6xxx_extrusions),
                ("E-7xxx-extrusions-PC1", electrical_7xxx_extrusions),
                ("E-8xxx-extrusions-PC1", electrical_8xxx_extrusions)
            ]:
                if alloys:
                    contamination = "A"
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": contamination,
                        "separation_group": "E-Non1xxx-extrusions"
                    })

        # Group electrical non-extrusions by family
        if electrical_nonextrusions_all:
            for family_name, alloys in [
                ("E-1xxx-PC1", electrical_1xxx_other),
                ("E-2xxx-PC1", electrical_2xxx_other),
                ("E-3xxx-PC1", electrical_3xxx_other),
                ("E-4xxx-PC1", electrical_4xxx_other),
                ("E-5xxx-PC1", electrical_5xxx_other),
                ("E-6xxx-PC1", electrical_6xxx_other),
                ("E-7xxx-PC1", electrical_7xxx_other),
                ("E-8xxx-PC1", electrical_8xxx_other),
                ("E-Castings-PC1", electrical_castings_grp)
            ]:
                if alloys:
                    contamination = "A"
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": contamination,
                        "separation_group": "E-Other"
                    })
        
        # Machinery group - separate by alloy family
        if machinery_all:
            for family_name, alloys in [
                ("ME-1xxx-PC1", machinery_1xxx),
                ("ME-2xxx-PC1", machinery_2xxx),
                ("ME-3xxx-PC1", machinery_3xxx),
                ("ME-4xxx-PC1", machinery_4xxx),
                ("ME-5xxx-PC1", machinery_5xxx),
                ("ME-6xxx-PC1", machinery_6xxx),
                ("ME-7xxx-PC1", machinery_7xxx),
                ("ME-8xxx-PC1", machinery_8xxx),
                ("ME-Castings-PC1", machinery_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "ME"
                    })
        
        # UBC group - separate by alloy family (only 3xxx and 5xxx)
        if container_3xxx_body_sheet:
            to_mix.append({
                "alloys": container_3xxx_body_sheet,
                "label": "UBC-3xxx-PC1",
                "contamination": "A",
                "separation_group": "UBC"
            })
        if container_5xxx_lid_sheet:
            to_mix.append({
                "alloys": container_5xxx_lid_sheet,
                "label": "UBC-5xxx-PC1",
                "contamination": "A",
                "separation_group": "UBC"
            })
        
        # Container other group - separate by alloy family
        if container_other_all:
            for family_name, alloys in [
                ("CP-Other-1xxx-PC1", container_1xxx),
                ("CP-Other-2xxx-PC1", container_2xxx),
                ("CP-Other-3xxx-PC1", container_3xxx_other),
                ("CP-Other-4xxx-PC1", container_4xxx),
                ("CP-Other-5xxx-PC1", container_5xxx_other),
                ("CP-Other-6xxx-PC1", container_6xxx),
                ("CP-Other-7xxx-PC1", container_7xxx),
                ("CP-Other-8xxx-PC1", container_8xxx),
                ("CP-Other-Castings-PC1", container_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "CP-Other"
                    })
        
        # Other group - separate by alloy family
        if other_all:
            for family_name, alloys in [
                ("Other-1xxx-PC1", other_1xxx),
                ("Other-2xxx-PC1", other_2xxx),
                ("Other-3xxx-PC1", other_3xxx),
                ("Other-4xxx-PC1", other_4xxx),
                ("Other-5xxx-PC1", other_5xxx),
                ("Other-6xxx-PC1", other_6xxx),
                ("Other-7xxx-PC1", other_7xxx),
                ("Other-8xxx-PC1", other_8xxx),
                ("Other-Castings-PC1", other_castings_grp)
            ]:
                if alloys:
                    to_mix.append({
                        "alloys": alloys,
                        "label": family_name,
                        "contamination": "A",
                        "separation_group": "Other"
                    })
        
        # Filter out empty streams
        to_mix = [stream for stream in to_mix if stream["alloys"]]
    elif mix_scenario == "93% accuracy separation of all PC scrap":
        # Individual alloy streams with 93% separation accuracy - return empty to use individual mode
        to_mix = []
    elif mix_scenario == "93% accuracy separation of all PC scrap with no contamination":
        # Individual alloy streams with 93% separation accuracy - return empty to use individual mode
        to_mix = []
    elif mix_scenario == "100% accuracy separation of all PC scrap":
        # Individual alloy streams with 100% separation accuracy - return empty to use individual mode
        to_mix = []
    elif mix_scenario == "100% accuracy separation of all PC scrap with no contamination":
        # Individual alloy streams with 100% separation accuracy - return empty to use individual mode
        to_mix = []
    
    # Validate that each alloy appears at most once in to_mix
    if to_mix:  # Only validate if to_mix is not empty
        # Collect all alloys from all streams in to_mix
        alloys_in_mix = []
        for stream in to_mix:
            alloys_in_mix.extend(stream["alloys"])
        
        # Check for duplicated alloys
        alloy_counts = {}
        for alloy in alloys_in_mix:
            alloy_counts[alloy] = alloy_counts.get(alloy, 0) + 1
        
        duplicated_alloys = {alloy: count for alloy, count in alloy_counts.items() if count > 1}
        if duplicated_alloys:
            raise ValueError(f"Duplicated alloys in to_mix: {duplicated_alloys}")

    return to_mix

def calculate_emissions(emissions_data, model_vars=None, inputs=None, year=None):
    """Flexibly calculate emissions based on available data and model configuration
    
    Args:
        emissions_data: DataFrame with emissions factors indexed by process
        model_vars: Dictionary of Gurobi model variables (for optimization objective)
                   Keys: 'primary', 'secondary_virgin', 'scrap', 'secondary', etc.
        inputs: Dictionary from input_values (for post-optimization calculation)
                Contains num_scrap, scrap_source, prim_comp_df, etc.
        year: Year for emissions data lookup
    
    Returns:
        Gurobi expression (if model_vars provided) or float (if inputs provided)
    """
    if model_vars is None and inputs is None:
        raise ValueError("Must provide either model_vars (for optimization) or inputs (for calculation)")
    
    # Determine if we're in optimization mode or calculation mode
    optimization_mode = model_vars is not None
    
    # Extract available primary elements from emissions data
    available_elements = []
    element_names = ['Al', 'Cu', 'Mg', 'Mn', 'Si', 'Fe', 'Zn']
    emissions_factors = {}
    
    for elem in element_names:
        # Try multiple process name formats
        process_name_options = [
            f'Primary {elem}',
            f'Primary {elem} production',
            f'Primary {elem.lower()}',
            f'Primary {elem.lower()} production'
        ]
        
        for process_name in process_name_options:
            if process_name in emissions_data.index:
                emissions_factors[elem] = emissions_data.loc[process_name, year] if year else emissions_data.loc[process_name].iloc[0]
                available_elements.append(elem)
                break
    
    # Extract process emissions
    process_emissions = {}
    process_names = {
        'scrap_prep': 'Scrap',
        'sec_al': 'Secondary',
        'fc': 'Fractional',
        'libs': 'LIBS',
        'rolling': 'Sheet rolling',
        'blanking_stamping': 'Sheet blanking & stamping',
        'assembly': 'Vehicle assembly'
    }
    
    for key, prefix in process_names.items():
        matching = emissions_data.index[emissions_data.index.str.startswith(prefix)]
        if len(matching) > 0:
            process_emissions[key] = emissions_data.loc[matching[0], year] if year else emissions_data.loc[matching[0]].iloc[0]
        else:
            process_emissions[key] = 0.0  # If process not in data, use 0
    
    # Calculate emissions
    if optimization_mode:
        # Build Gurobi expression for optimization objective
        primary = model_vars['primary']
        secondary_virgin = model_vars['secondary_virgin']
        scrap = model_vars['scrap']
        secondary = model_vars['secondary']
        num_scrap = model_vars['num_scrap']
        scrap_source = model_vars['scrap_source']
        prim_comp = model_vars['prim_comp']
        
        # Map element names to indices in prim_comp
        # Need to get element order from prim_comp_df if available
        element_indices = {}
        if 'prim_comp_df' in model_vars and model_vars['prim_comp_df'] is not None:
            prim_comp_df = model_vars['prim_comp_df']
            for idx, row_name in enumerate(prim_comp_df.index):
                for elem in available_elements:
                    if elem in row_name and 'Primary' in row_name:
                        element_indices[elem] = idx
                        break
        else:
            # Fallback: assume standard order [Si, Fe, Cu, Mn, Mg, Cr, Ni, Zn, Ti]
            # Map only available elements
            standard_order = ['Al', 'Si', 'Fe', 'Cu', 'Mn', 'Mg', 'Cr', 'Ni', 'Zn', 'Ti']
            for elem in available_elements:
                if elem in standard_order[:len(prim_comp)]:
                    element_indices[elem] = standard_order.index(elem)
        
        emissions_expr = 0
        
        # Primary material emissions (for each element)
        for elem in available_elements:
            if elem in element_indices:
                idx = element_indices[elem]
                emissions_expr += emissions_factors[elem] * primary.sum("*", idx)
                emissions_expr += emissions_factors[elem] * secondary_virgin.sum("*", idx)
        
        # Scrap preparation emissions
        if process_emissions['scrap_prep'] != 0:
            emissions_expr += process_emissions['scrap_prep'] * (scrap.sum("*", 0) + sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source)))
        
        # Secondary aluminum production emissions
        if process_emissions['sec_al'] != 0:
            emissions_expr += process_emissions['sec_al'] * secondary.sum()
        
        # Fractional crystallization emissions (only for separated scrap)
        if process_emissions['fc'] != 0:
            emissions_expr += process_emissions['fc'] * sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))
        
        # LIBS emissions (if applicable)
        if process_emissions['libs'] != 0:
            emissions_expr += process_emissions['libs'] * sum(scrap.sum("*", i) for i in range(num_scrap, scrap_source))
        
        return emissions_expr
    
    else:
        # Post-optimization calculation mode - extract values from optimization results
        gurobi_output = inputs['gurobi_output']
        num_scrap = inputs['num_scrap']
        
        # Extract variables from gurobi_output
        primary = {k: v for k, v in gurobi_output.items() if 'Primary Material' in k}
        scrap = {k: v for k, v in gurobi_output.items() if 'Scrap Material' in k}
        secondary_virgin = {k: v for k, v in gurobi_output.items() if 'Virgin Material for Secondary Production' in k}
        secondary = {k: v for k, v in gurobi_output.items() if 'Secondary Material Total' in k}
        
        # Get prim_comp_df to map elements to indices
        prim_comp_df = inputs.get('prim_comp_df', None)
        scrap_source = inputs.get('scrap_source', num_scrap)
        eol_scrapsum_RR = inputs.get('eol_scrapsum_RR', None)
        demand = inputs.get('demand', None)
        
        # Map element names to indices
        element_indices = {}
        if prim_comp_df is not None and available_elements:
            for idx, row_name in enumerate(prim_comp_df.index):
                # Match elements more flexibly - check if element appears in row name
                for elem in available_elements:
                    # Check various patterns: "Primary Al", "Primary Aluminum", or just element name
                    if (elem in row_name or 
                        (elem == 'Al' and 'Aluminum' in row_name) or
                        (elem == 'Si' and 'Silicon' in row_name) or
                        (elem == 'Fe' and 'Iron' in row_name) or
                        (elem == 'Cu' and 'Copper' in row_name) or
                        (elem == 'Mn' and 'Manganese' in row_name) or
                        (elem == 'Mg' and 'Magnesium' in row_name) or
                        (elem == 'Zn' and 'Zinc' in row_name)):
                        element_indices[elem] = idx
                        break
        
        emissions_total = 0.0
        
        # Primary material emissions
        for elem in available_elements:
            if elem in element_indices:
                idx = element_indices[elem]
                # Sum all primary[i, idx] where i is product index
                primary_elem = sum(v[0] for k, v in primary.items() if f',{idx}]' in k)
                sec_virg_elem = sum(v[0] for k, v in secondary_virgin.items() if f',{idx}]' in k)
                elem_emissions = emissions_factors[elem] * (primary_elem + sec_virg_elem)
                emissions_total += elem_emissions
        # print(f"       Cumulative emissions: {emissions_total:.2f} kg CO2-eq")
        
        # Scrap preparation emissions (EOL scrap only)
        if process_emissions['scrap_prep'] != 0 and eol_scrapsum_RR is not None:
            scrap_prep_emissions = process_emissions['scrap_prep'] * eol_scrapsum_RR
            emissions_total += scrap_prep_emissions
        
        # Secondary aluminum production emissions
        if process_emissions['sec_al'] != 0:
            secondary_total = sum(v[0] for v in secondary.values())
            sec_al_emissions = process_emissions['sec_al'] * secondary_total
            emissions_total += sec_al_emissions
        
        # Fractional crystallization emissions (only separated scrap)
        # print(f"\n--- Fractional Crystallization Emissions ---")
        if process_emissions['fc'] != 0:
            scrap_separated = sum(v[0] for k, v in scrap.items() if any(f',{i}]' in k for i in range(num_scrap, scrap_source)))
            fc_emissions = process_emissions['fc'] * scrap_separated
            emissions_total += fc_emissions
            # print(f"  Separated scrap: {scrap_separated:.2f} kg × {process_emissions['fc']:.3f} = {fc_emissions:.2f} kg CO2-eq")
        # else:
            # print(f"  FC factor is 0, skipping")
        
        # LIBS emissions (if applicable)
        if process_emissions['libs'] != 0:
            scrap_separated = sum(v[0] for k, v in scrap.items() if any(f',{i}]' in k for i in range(num_scrap, scrap_source)))
            libs_emissions = process_emissions['libs'] * scrap_separated
            emissions_total += libs_emissions
        
        # Rolling emissions (based on demand)
        if process_emissions['rolling'] != 0 and demand is not None:
            total_demand = sum(demand)
            rolling_amount = 0.66 * total_demand
            rolling_emissions = process_emissions['rolling'] * rolling_amount
            emissions_total += rolling_emissions
        
        # Blanking & Stamping emissions (based on demand)
        if process_emissions['blanking_stamping'] != 0 and demand is not None:
            total_demand = sum(demand)
            blanking_amount = 0.66 * 0.95 * 0.70 * total_demand
            blanking_emissions = process_emissions['blanking_stamping'] * blanking_amount
            emissions_total += blanking_emissions
        
        # Assembly emissions (based on demand)
        if process_emissions['assembly'] != 0 and demand is not None:
            total_demand = sum(demand)
            assembly_amount = 0.66 * 0.95 * 0.70 * 0.995 * total_demand
            assembly_emissions = process_emissions['assembly'] * assembly_amount
            emissions_total += assembly_emissions
        
        # Convert from kg CO2-e to kt CO2-e
        return emissions_total / 1_000_000

def result_calculations(model, year, scenario):
    """Function that calculates key values from the optimization output

    Args:
        model: Gurobi optimization model after optimization
        year: Year of the scenario (e.g., 2025, 2030, 2035, 2040)
        scenario: Name of the scenario (e.g., "Baseline", "93% accuracy PC scrap separation by family and mid contamination", etc.)
    
    Returns:
        dict: Dictionary of calculated results (e.g., emissions, scrap usage, etc.)    
    """

    input = input_values(year, scenario)
    num_scrap = input["num_scrap"]
    furnace_yield = input["furnace_yield"]
    demand = input["demand"]
    forming_scrap_df = input["forming_scrap_df"]
    fabrication_scrap_df = input["fabrication_scrap_df"]
    eol_scrap_df = input["eol_scrap_df"]
    eol_scrap_comps = input["eol_scrap_comps"]
    product_list = input["product_list"]
    
    # Get original scrap quantities before collection rates and scrap processing yields
    loaded_data = load_data(year, scenario)
    forming_scrap_by_alloy_data = loaded_data["forming_scrap_by_alloy_data"]
    fabrication_scrap_by_alloy_data = loaded_data["fabrication_scrap_by_alloy_data"]
    supply_data = loaded_data["supply_data"]
    
    # Get original quantities for the specified year
    original_forming_scrap = forming_scrap_by_alloy_data[year]
    original_fabrication_scrap = fabrication_scrap_by_alloy_data[year]
    # Filter original_eol_scrap to only include product_list alloys
    all_original_eol_scrap = supply_data[year]
    original_eol_scrap = all_original_eol_scrap[all_original_eol_scrap.index.isin(product_list)]
    
    gurobi_output = {}
    for v in model.getVars():
        gurobi_output[v.varName] = [v.x]

    # Extract and process key variables and information from gurobi_output
    primary = {k: v for k, v in gurobi_output.items() if 'Primary' in k}
    scrap = {k: v for k, v in gurobi_output.items() if 'Scrap Material' in k}
    unsep = {k: v for k, v in gurobi_output.items() if 'Scrap Supply TOTAL' in k}
    sep = {k: v for k, v in gurobi_output.items() if 'Separated Supply TOTAL' in k}
    secondary_virgin = {k: v for k, v in gurobi_output.items() if 'Virgin' in k and 'Secondary' in k}
    secondary = {k: v for k, v in gurobi_output.items() if 'Secondary' in k and 'Total' not in k}

    # Scrap values are labeled with indices [x, y], where x is the index of the product alloy [0, num_products - 1] and 
    # y is the index of the scrap stream [0, num_scrap -1]. 
    # Scrap streams 0 through len(forming_scrap_df) - 1 are forming scrap streams
    # Scrap streams len(forming_scrap_df) through len(forming_scrap_df) + len(fabrication_scrap_df) - 1 are fabrication scrap streams
    # Scrap streams len(forming_scrap_df) + len(fabrication_scrap_df) through len(forming_scrap_df) + len(fabrication_scrap_df) + len(eol_scrap_df) - 1 are EOL scrap streams.

    # Extract forming scrap streams from scrap dictionary
    forming_indices = range(len(forming_scrap_df))
    forming_pattern = re.compile(r",(" + "|".join(str(i) for i in forming_indices) + r")\]")
    forming_scrap = {k: v for k, v in scrap.items() if forming_pattern.search(k)}

    # Extract fabrication scrap streams from scrap dictionary
    fabrication_indices = range(len(forming_scrap_df), len(forming_scrap_df) + len(fabrication_scrap_df))
    fabrication_pattern = re.compile(r",(" + "|".join(str(i) for i in fabrication_indices) + r")\]")
    fabrication_scrap = {k: v for k, v in scrap.items() if fabrication_pattern.search(k)}

    # Extract EOL scrap streams from scrap dictionary
    eol_indices = range(len(forming_scrap_df) + len(fabrication_scrap_df), num_scrap)
    eol_pattern = re.compile(r",(" + "|".join(str(i) for i in eol_indices) + r")\]")
    eol_scrap = {k: v for k, v in scrap.items() if eol_pattern.search(k)}

    # Sum of all primary material used before and after melt loss
    prim_preML = sum(v[0] for v in primary.values())
    prim_postML = furnace_yield * prim_preML

    print("Total amount of primary virgin material used (before melt loss): %g" % prim_preML)
    print("Total amount of primary virgin material used (post melt loss): %g \n" % prim_postML)

    # Sum of all secondary virgin material used before and after melt loss
    secvirg_preML = sum(v[0] for v in secondary_virgin.values())
    secvirg_postML = furnace_yield * secvirg_preML

    print("Total amount of secondary virgin material used (before melt loss): %g" % secvirg_preML)
    print("Total amount of secondary virgin material used (post melt loss): %g \n" % secvirg_postML)


    # Sum of forming scrap used
    forming_scrapsum_RR = sum(v[0] for v in forming_scrap.values())
    forming_scrapsum_RC = forming_scrapsum_RR * furnace_yield

    totalforming = sum(forming_scrap_df)
    original_totalforming = sum(original_forming_scrap)

    print("Total forming scrap used (before melt loss): %g" % forming_scrapsum_RR)
    print("Total forming scrap used (post melt loss): %g" % forming_scrapsum_RC)
    print("Total forming scrap generated: %g" % original_totalforming)
    print("Total forming scrap available: %g \n" % totalforming)

    fabrication_scrapsum_RR = sum(v[0] for v in fabrication_scrap.values())
    fabrication_scrapsum_RC = fabrication_scrapsum_RR * furnace_yield

    totalfabrication = sum(fabrication_scrap_df)
    original_totalfabrication = sum(original_fabrication_scrap)

    print("Total fabrication scrap used (before melt loss): %g" % fabrication_scrapsum_RR)
    print("Total fabrication scrap used (post melt loss): %g" % fabrication_scrapsum_RC)
    print("Total fabrication scrap generated: %g" % original_totalfabrication)
    print("Total fabrication scrap available: %g \n" % totalfabrication)

    eol_scrapsum_RR = sum(v[0] for v in eol_scrap.values())
    eol_scrapsum_RC = eol_scrapsum_RR * furnace_yield

    totaleol = sum(eol_scrap_df)
    original_totaleol = sum(original_eol_scrap)

    print("Total EOL scrap used (before melt loss): %g" % eol_scrapsum_RR)
    print("Total EOL scrap used (post melt loss): %g" % eol_scrapsum_RC)
    print("Total EOL scrap generated: %g" % original_totaleol)
    print("Total EOL scrap available: %g \n" % totaleol)

    scrapsum_RR = forming_scrapsum_RR + fabrication_scrapsum_RR + eol_scrapsum_RR
    scrapsum_RC = forming_scrapsum_RC + fabrication_scrapsum_RC + eol_scrapsum_RC
    total_scrap = totalforming + totalfabrication + totaleol
    original_total_scrap = original_totalforming + original_totalfabrication + original_totaleol

    print("Total scrap used (before melt loss): %g" % scrapsum_RR)
    print("Total scrap used (post melt loss): %g" % scrapsum_RC)
    print("Total scrap generated: %g" % original_total_scrap)
    print("Total scrap available: %g \n" % total_scrap)

    total_demand = sum(demand)

    print("Total demand: %g \n" % total_demand)


    RR = scrapsum_RR/original_total_scrap
    RC = scrapsum_RC/total_demand

    RR_EOL = eol_scrapsum_RR/original_totaleol
    RC_EOL = eol_scrapsum_RC/total_demand

    RR_forming = forming_scrapsum_RR/original_totalforming
    RC_forming = forming_scrapsum_RC/total_demand

    RR_fabrication = fabrication_scrapsum_RR/original_totalfabrication
    RC_fabrication = fabrication_scrapsum_RC/total_demand

    RR_manuf = (forming_scrapsum_RR + fabrication_scrapsum_RR) / (original_totalforming + original_totalfabrication)
    RC_manuf = (forming_scrapsum_RC + fabrication_scrapsum_RC) / total_demand

    print("RR: %g" % RR)
    print("RC: %g" % RC)
    print("EOL scrap RR: %g" % RR_EOL)
    print("EOL scrap RC: %g" % RC_EOL)
    print("Forming scrap RR: %g" % RR_forming)
    print("Forming scrap RC: %g" % RC_forming)
    print("Fabrication scrap RR: %g" % RR_fabrication)
    print("Fabrication scrap RC: %g" % RC_fabrication)
    print("Manufacturing scrap RR: %g" % RR_manuf)
    print("Manufacturing scrap RC: %g" % RC_manuf)

    # Calculate individual scrap stream RR and RC values
    # Get necessary data for individual stream calculations
    product_list = input["product_list"]
    forming_scrap_df = input["forming_scrap_df"]
    fabrication_scrap_df = input["fabrication_scrap_df"]
    eol_scrap_df = input["eol_scrap_df"]
    
    # For EOL mixed streams, calculate the "generated" mixed stream quantities
    # before collection rates and scrap processing yields are applied
    eol_mix_scenario = loaded_data["scenario_row"]["eol_mix_scenario"].values[0]
    eol_to_mix = build_mix_list(product_list, eol_mix_scenario)
    
    # Calculate EOL mixed stream generated quantities (before collection rates/processing yields)
    eol_generated_mixed_quantities = {}
    if eol_to_mix:  # If there are mixed streams
        supply_data_current = loaded_data["supply_data"]
        all_eolscrap_generated = supply_data_current[year]  # Before collection rates and processing yields
        mix_eol_fraction = input["mix_eol_fraction"]
        
        for stream in eol_to_mix:
            if "label" in stream:
                # Calculate the generated quantity for this mixed stream (before collection rates and processing yields)
                alloys = stream["alloys"]
                # Sum up the original quantities of constituent alloys
                generated_quantity = sum(all_eolscrap_generated[alloy] * mix_eol_fraction[alloy] 
                                       for alloy in alloys if alloy in all_eolscrap_generated.index and alloy in mix_eol_fraction.index)
                eol_generated_mixed_quantities[stream["label"]] = generated_quantity
    
    # Calculate individual stream RR and RC values
    individual_stream_rr = {}
    individual_stream_rc = {}
    
    # For forming scrap streams
    forming_scrap_labels = forming_scrap_df.index.tolist()
    for forming_label in forming_scrap_labels:
        # Get used quantity from optimization results
        used_quantity = 0
        # Sum across all products for this scrap stream
        forming_idx = forming_scrap_labels.index(forming_label)
        for prod_idx in range(input["num_products"]):
            var_name = f"Scrap Material[{prod_idx},{forming_idx}]"
            if var_name in gurobi_output:
                used_quantity += gurobi_output[var_name][0]
        
        # For mixed streams, use the quantity from forming_scrap_df (generated mixed stream quantity)
        # For individual streams, use original scrap generation
        if forming_label in forming_scrap_df.index:
            generated_quantity = forming_scrap_df[forming_label]
        else:
            # Try to find individual alloy in original data
            alloy_name = forming_label.replace(' forming scrap', '')
            if alloy_name in original_forming_scrap.index:
                generated_quantity = original_forming_scrap[alloy_name]
            else:
                generated_quantity = 0
        
        if generated_quantity > 0:
            individual_stream_rr[forming_label] = used_quantity / generated_quantity
        else:
            individual_stream_rr[forming_label] = 0
        individual_stream_rc[forming_label] = (used_quantity * furnace_yield) / total_demand
    
    # For fabrication scrap streams
    fabrication_scrap_labels = fabrication_scrap_df.index.tolist()
    for fabrication_label in fabrication_scrap_labels:
        # Get used quantity from optimization results
        used_quantity = 0
        # Sum across all products for this scrap stream
        fabrication_idx = len(forming_scrap_labels) + fabrication_scrap_labels.index(fabrication_label)
        for prod_idx in range(input["num_products"]):
            var_name = f"Scrap Material[{prod_idx},{fabrication_idx}]"
            if var_name in gurobi_output:
                used_quantity += gurobi_output[var_name][0]
        
        # For mixed streams, use the quantity from fabrication_scrap_df (generated mixed stream quantity)
        # For individual streams, use original scrap generation
        if fabrication_label in fabrication_scrap_df.index:
            generated_quantity = fabrication_scrap_df[fabrication_label]
        else:
            # Try to find individual alloy in original data
            alloy_name = fabrication_label.replace(' fabrication scrap', '')
            if alloy_name in original_fabrication_scrap.index:
                generated_quantity = original_fabrication_scrap[alloy_name]
            else:
                generated_quantity = 0
        
        if generated_quantity > 0:
            individual_stream_rr[fabrication_label] = used_quantity / generated_quantity
        else:
            individual_stream_rr[fabrication_label] = 0
        individual_stream_rc[fabrication_label] = (used_quantity * furnace_yield) / total_demand
    
    # For EOL scrap streams
    eol_scrap_labels = eol_scrap_df.index.tolist()
    for eol_label in eol_scrap_labels:
        # Get used quantity from optimization results
        used_quantity = 0
        # Sum across all products for this scrap stream
        eol_idx = len(forming_scrap_labels) + len(fabrication_scrap_labels) + eol_scrap_labels.index(eol_label)
        for prod_idx in range(input["num_products"]):
            var_name = f"Scrap Material[{prod_idx},{eol_idx}]"
            if var_name in gurobi_output:
                used_quantity += gurobi_output[var_name][0]
        
        # For EOL streams, we need the generated quantity (before collection rates/processing yields)
        generated_quantity = 0
        
        if eol_label in eol_generated_mixed_quantities:
            # This is a mixed EOL stream
            generated_quantity = eol_generated_mixed_quantities[eol_label]
        elif eol_label in eol_scrap_df.index:
            # This could be an individual stream that appears in the DataFrame
            # Need to find the corresponding original quantity
            alloy_name = eol_label.replace(' EOL scrap', '')
            if alloy_name in original_eol_scrap.index:
                generated_quantity = original_eol_scrap[alloy_name]
        else:
            # Try to find individual alloy in original data
            alloy_name = eol_label.replace(' EOL scrap', '')
            if alloy_name in original_eol_scrap.index:
                generated_quantity = original_eol_scrap[alloy_name]
        
        if generated_quantity > 0:
            individual_stream_rr[eol_label] = used_quantity / generated_quantity
        else:
            individual_stream_rr[eol_label] = 0
        individual_stream_rc[eol_label] = (used_quantity * furnace_yield) / total_demand

    ##################################################################################################################################
    '''Calculate emissions for all three scenarios (frozen, moderate, aggressive)'''
    ##################################################################################################################################
    
    # Prepare inputs for emissions calculation
    emissions_inputs = {
        'gurobi_output': gurobi_output,
        'num_scrap': num_scrap,
        'scrap_source': input.get('scrap_source', num_scrap),
        'prim_comp_df': input.get('prim_comp_df', None),
        'eol_scrapsum_RR': eol_scrapsum_RR,
        'demand': input.get('demand', None)
    }
    
    # Calculate emissions for each scenario
    emissions_frozen = calculate_emissions(
        input["emissions_data_frozen"],
        inputs=emissions_inputs,
        year=year
    )
    
    emissions_moderate = calculate_emissions(
        input["emissions_data_moderate"],
        inputs=emissions_inputs,
        year=year
    )
    
    emissions_aggressive = calculate_emissions(
        input["emissions_data_aggressive"],
        inputs=emissions_inputs,
        year=year
    )
    
    print(f"Emissions (frozen): {emissions_frozen:.6f} kt CO2-eq")
    print(f"Emissions (moderate): {emissions_moderate:.6f} kt CO2-eq")
    print(f"Emissions (aggressive): {emissions_aggressive:.6f} kt CO2-eq")

    output = {
        "prim_preML": prim_preML,
        "secvirg_preML": secvirg_preML,
        "forming_scrapsum_RR": forming_scrapsum_RR,
        "fabrication_scrapsum_RR": fabrication_scrapsum_RR,
        "eol_scrapsum_RR": eol_scrapsum_RR,
        "RR": RR,
        "RC": RC,
        "RR_EOL": RR_EOL,
        "RC_EOL": RC_EOL,
        "RR_forming": RR_forming,
        "RC_forming": RC_forming,
        "RR_fabrication": RR_fabrication,
        "RC_fabrication": RC_fabrication,
        "RR_manuf": RR_manuf,
        "RC_manuf": RC_manuf,
        "individual_stream_rr": individual_stream_rr,
        "individual_stream_rc": individual_stream_rc,
        "forming_scrap_labels": forming_scrap_labels,
        "fabrication_scrap_labels": fabrication_scrap_labels,
        "eol_scrap_labels": eol_scrap_labels,
        "emissions_frozen": emissions_frozen,
        "emissions_moderate": emissions_moderate,
        "emissions_aggressive": emissions_aggressive,
        "eol_scrap_comps": eol_scrap_comps
    }

    return output

def constraints_hierarchy(model, year, scenario):
    """Function that filters constraints for binding constraints and ranks them by impact on objective
    
    Args:
        model: Gurobi optimization model after optimization
        year: Year of the scenario (e.g., 2025, 2030, 2035, 2040)
        scenario: Name of the scenario (e.g., "Baseline", "93% accuracy PC scrap separation by family and mid contamination", etc.)

    Returns:
        DataFrame: Ranked constraints with columns for constraint name, max estimated impact on objective, slack, shadow price, allowable decrease, and allowable increase
    """
    input = input_values(year, scenario)
    product_list = input["product_list"]
    elements_list = input["elements_list"]

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

        # Decode the name by replacing the indices with actual values
        if "[" in name and "]" in name:
            try:
                indices = name[name.index("[") + 1 : name.index("]")].split(",")
                a, b = int(indices[0]), int(indices[1])
                if "composition" in name:
                    if "Lower" in name:
                        low_or_up = "Lower"
                    elif "Upper" in name:
                        low_or_up = "Upper"
                    if "secondary" in name:
                        prim_or_sec = "secondary"
                    elif "primary" in name:
                        prim_or_sec = "primary"
                    decoded_name = f"{low_or_up} bound on {elements_list[b]} in {product_list[a]}({prim_or_sec} production)"
                else:
                    decoded_name = name  # If it's not a composition constraint, keep the original name
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
    # for _, row in ranked_constraints_df.iterrows():
    #     # print(f"{row['Name']}: Max est. impact = {row['Max est. impact']:.4f}, Shadow Price = {row['Shadow Price']:.4f}, Allowable decrease = {row['Allowable decrease']:.4f}, Allowable increase = {row['Allowable increase']:.4f}")
    #     print(f"{row['Name']}: Max est. impact = {row['Max est. impact']}, Slack = {row['Slack']}, Shadow Price = {row['Shadow Price']}, Allowable decrease = {row['Allowable decrease']}, Allowable increase = {row['Allowable increase']}")
    
    print("Ranked constraints generated.")

    return ranked_constraints_df

def check_alternative_solutions(model, year=None, scenario=None):
    """Check whether the optimization model has a unique solution or potential alternative solutions.
    
    Args:
        model: Gurobi model object that has been optimized
        year: Year for scenario (optional, needed for variable name decoding)
        scenario: Scenario name (optional, needed for variable name decoding)
        
    Returns:
        tuple: (is_unique, zero_reduced_cost_vars)
            - is_unique (bool): True if solution is unique, False if potential alternatives exist
            - zero_reduced_cost_vars (list): List of decoded variable names with zero reduced cost (non-basic variables)
    """
    
    if model.status != gp.GRB.OPTIMAL:
        return None, ["Model did not reach optimal solution"]
    
    # Check for primal degeneracy (multiple optimal solutions)
    # Look for non-basic variables with zero reduced costs
    zero_reduced_cost_vars = []
    decoded_vars = []
    
    # Get labels for decoding if year and scenario are provided
    if year is not None and scenario is not None:
        try:
            inputs = input_values(year, scenario)
            product_list = inputs["product_list"]
            
            # Get results output to access the label lists
            results_output = result_calculations(model, year, scenario)
            forming_scrap_labels = results_output["forming_scrap_labels"]
            fabrication_scrap_labels = results_output["fabrication_scrap_labels"] 
            eol_scrap_labels = results_output["eol_scrap_labels"]
            
            # Get primary composition data for virgin labels
            prim_comp_df = inputs["prim_comp_df"]
            primary_virgin_labels = prim_comp_df.index.tolist()
            secondary_virgin_labels = [label.replace("Primary", "Secondary Virgin") for label in primary_virgin_labels]
            
            # Combine all scrap labels in order: forming, fabrication, EOL
            all_scrap_labels = forming_scrap_labels + fabrication_scrap_labels + eol_scrap_labels
            
        except Exception as e:
            print(f"Warning: Could not load labels for decoding: {e}")
            product_list = None
            primary_virgin_labels = None
            secondary_virgin_labels = None
            all_scrap_labels = None
    else:
        product_list = None
        primary_virgin_labels = None
        secondary_virgin_labels = None
        all_scrap_labels = None
    
    for var in model.getVars():
        if var.x == 0 and abs(var.rc) < 1e-9:  # Non-basic with zero reduced cost
            var_name = var.varName
            zero_reduced_cost_vars.append(var_name)
            
            # Decode variable name if labels are available
            decoded_name = var_name  # Default to original name
            if product_list is not None:
                # Extract indices from variable name like "Primary Material[1,2]"
                match = re.search(r'\[(\d+),(\d+)\]$', var_name)
                if match:
                    prod_idx, source_idx = int(match.group(1)), int(match.group(2))
                    
                    try:
                        if var_name.startswith("Primary Material"):
                            if primary_virgin_labels and prod_idx < len(product_list) and source_idx < len(primary_virgin_labels):
                                decoded_name = f"{primary_virgin_labels[source_idx]} in {product_list[prod_idx]}"
                        
                        elif var_name.startswith("Virgin Material for Secondary"):
                            if secondary_virgin_labels and prod_idx < len(product_list) and source_idx < len(secondary_virgin_labels):
                                decoded_name = f"{secondary_virgin_labels[source_idx]} in {product_list[prod_idx]}"
                        
                        elif var_name.startswith("Scrap Material"):
                            if all_scrap_labels and prod_idx < len(product_list) and source_idx < len(all_scrap_labels):
                                decoded_name = f"{all_scrap_labels[source_idx]} in {product_list[prod_idx]}"
                    
                    except IndexError:
                        # If indices are out of range, keep original name
                        pass
            
            decoded_vars.append(decoded_name)
    
    is_unique = len(zero_reduced_cost_vars) == 0
    
    if is_unique:
        print("Unique optimal solution found")
    else:
        print("Potential alternative optimal solutions exist")
        print(f"Variables with zero reduced cost: {zero_reduced_cost_vars}")
        if decoded_vars != zero_reduced_cost_vars:
            print(f"Decoded variable names: {decoded_vars}")
    
    return is_unique, decoded_vars

def generate_snapshot(model, year, scenario, folder):
    """Function that generates output Excel workbook with relevant results and data
    
    Args:
        model: Gurobi optimization model after optimization
        year: Year of the scenario (e.g., 2025, 2030, 2035, 2040)
        scenario: Name of the scenario (e.g., "Baseline", "93% accuracy PC scrap separation by family and mid contamination", etc.)
        folder: Name of the folder to save the output workbook in (e.g., "snapshots")

    Returns:
        None (saves an Excel workbook with results and data in the specified folder)    
    """
    # Get scenario label from loaded data function
    loaded_data = load_data(year, scenario)
    label = loaded_data["label"]

    # Load the input data
    input = input_values(year, scenario)
    forming_scrap_list = input["forming_scrap_df"].index.tolist()
    fabrication_scrap_list = input["fabrication_scrap_df"].index.tolist()
    eol_scrap_list = input["eol_scrap_df"].index.tolist()
    elements_list = input["elements_list"]
    product_list = input["product_list"]
    furnace_yield = input["furnace_yield"]
    demand = input["demand"]
    prim_comp_df = input["prim_comp_df"]
    forming_scrap_df = input["forming_scrap_df"]
    fabrication_scrap_df = input["fabrication_scrap_df"]
    eol_scrap_df = input["eol_scrap_df"]
    scrap_comp_df = input["scrap_comp_df"]

    # Get calculated results including individual stream RR and RC values
    results_output = result_calculations(model, year, scenario)
    demand = input["demand"]
    prim_comp_df = input["prim_comp_df"]
    forming_scrap_df = input["forming_scrap_df"]
    fabrication_scrap_df = input["fabrication_scrap_df"]
    eol_scrap_df = input["eol_scrap_df"]
    scrap_comp_df = input["scrap_comp_df"]

    # Initialize the output dictionary
    output = {}
    for v in model.getVars():
        output[v.varName] = [v.x]

    
    # Run constraints hierarchy function to get ranked constraints
    ranked_constraints_df = constraints_hierarchy(model, year, scenario)

    ##################################################################################################################################
    """Generate results output table showing material flows by product and stream"""
    ##################################################################################################################################
    
    # Construct column labels
    primary_virgin_labels = prim_comp_df.index.tolist()
    secondary_virgin_labels = [label.replace("Primary", "Secondary Virgin") for label in primary_virgin_labels]
    forming_scrap_labels = [f"{label}" for label in forming_scrap_list]
    fabrication_scrap_labels = [f"{label}" for label in fabrication_scrap_list]
    eol_scrap_labels = [f"{label}" for label in eol_scrap_list]
    
    columns = (primary_virgin_labels + 
               secondary_virgin_labels + 
               forming_scrap_labels + 
               fabrication_scrap_labels + 
               eol_scrap_labels
               )

    # Initialize DataFrame to hold results
    results_df = pd.DataFrame(index=product_list, columns=columns)
    
    # Fill in the DataFrame with results from output dictionary
    for key, value in output.items():
        # Check if 'value' is a list and has exactly one element
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (int, float)):
            value = value[0]  # Extract the single float from the list
        
        if isinstance(value, (int, float)):  # Ensure extracted value is a number
            if 'Primary Material' in key:
                i, j = map(int, key[len('Primary Material') + 1 : -1].split(','))
                results_df.iat[i, j] = value
            elif 'Virgin Material for Secondary Production' in key:
                i, j = map(int, key[len('Virgin Material for Secondary Production') + 1 : -1].split(','))
                j += len(primary_virgin_labels)
                results_df.iat[i, j] = value
            elif 'Scrap Material' in key:
                i, j = map(int, key[len('Scrap Material') + 1 : -1].split(','))
                j += len(primary_virgin_labels) + len(secondary_virgin_labels)
                results_df.iat[i, j] = value
        else:
            print(f"Skipping key '{key}' with unexpected value type or structure: {value}")

    # Calculate row sums and append as a new column
    results_df['Total Material Used in Furnace'] = results_df.sum(axis=1)

    # Calculate total material used across all products (total material used in furnace adjusted for furnace yield)
    results_df['Total Material Used (adjusted for furnace yield)'] = results_df['Total Material Used in Furnace'] * furnace_yield

    # Calculate and append column sums (excludes new columns)
    column_sums = results_df.iloc[:, :-2].sum()
    # Create new row as a series for the column totals
    total_row = column_sums.rename('TOTAL').to_frame().T
    # Append the total row to the DataFrame
    results_df = pd.concat([results_df, total_row])

    # Add individual scrap stream RR and RC calculations using pre-calculated values
    
    # Extract pre-calculated individual stream RR and RC values
    individual_stream_rr = results_output["individual_stream_rr"]
    individual_stream_rc = results_output["individual_stream_rc"]
    forming_scrap_labels = results_output["forming_scrap_labels"]
    fabrication_scrap_labels = results_output["fabrication_scrap_labels"]
    eol_scrap_labels = results_output["eol_scrap_labels"]
    
    # Create empty row
    empty_row = pd.Series(index=results_df.columns, dtype=float, name='')
    results_df = pd.concat([results_df, empty_row.to_frame().T])
    
    # Create RR and RC rows
    rr_row = pd.Series(index=results_df.columns, dtype=float, name='RR')
    rc_row = pd.Series(index=results_df.columns, dtype=float, name='RC')
    
    # Calculate column indices for scrap streams
    scrap_columns_start = len(primary_virgin_labels) + len(secondary_virgin_labels)
    
    # Populate RR and RC rows using pre-calculated values
    # For forming scrap streams
    for i, forming_label in enumerate(forming_scrap_labels):
        col_idx = scrap_columns_start + i
        if col_idx < len(results_df.columns) - 2:  # Exclude the two summary columns
            if forming_label in individual_stream_rr:
                rr_row.iloc[col_idx] = individual_stream_rr[forming_label]
            if forming_label in individual_stream_rc:
                rc_row.iloc[col_idx] = individual_stream_rc[forming_label]
    
    # For fabrication scrap streams  
    for i, fabrication_label in enumerate(fabrication_scrap_labels):
        col_idx = scrap_columns_start + len(forming_scrap_labels) + i
        if col_idx < len(results_df.columns) - 2:
            if fabrication_label in individual_stream_rr:
                rr_row.iloc[col_idx] = individual_stream_rr[fabrication_label]
            if fabrication_label in individual_stream_rc:
                rc_row.iloc[col_idx] = individual_stream_rc[fabrication_label]
    
    # For EOL scrap streams
    for i, eol_label in enumerate(eol_scrap_labels):
        col_idx = scrap_columns_start + len(forming_scrap_labels) + len(fabrication_scrap_labels) + i
        if col_idx < len(results_df.columns) - 2:
            if eol_label in individual_stream_rr:
                rr_row.iloc[col_idx] = individual_stream_rr[eol_label]
            if eol_label in individual_stream_rc:
                rc_row.iloc[col_idx] = individual_stream_rc[eol_label]
    
    # Append RR and RC rows to the DataFrame
    results_df = pd.concat([results_df, rr_row.to_frame().T])
    results_df = pd.concat([results_df, rc_row.to_frame().T])

    ##################################################################################################################################
    """Generate list of DMFA inputs"""
    ##################################################################################################################################

    # Create DataFrame for "Demand alloys"
    demand_alloys_df = pd.DataFrame({'Demand Alloys': product_list, str(year): demand})

    ##################################################################################################################################
    """Build output file with all results"""
    ##################################################################################################################################

    filepath = folder + str(year) + '_domesticABS_' + label + '.xlsx'

    # Check for alternative solutions
    is_unique, zero_reduced_cost_vars = check_alternative_solutions(model, year, scenario)

    # Set up ExcelWriter
    with pd.ExcelWriter(filepath, engine='xlsxwriter') as writer:
        # RESULTS sheet
        results_df.to_excel(writer, sheet_name='RESULTS', index=True)

        # RANKED CONSTRAINTS sheet
        ranked_constraints_df.to_excel(writer, sheet_name='RANKED CONSTRAINTS', index=False)

        # ALT. SOLUTIONS sheet
        if is_unique:
            # Create a simple DataFrame with the unique solution message
            alt_solutions_df = pd.DataFrame({'Solution Status': ['Optimized solution is unique.']})
            alt_solutions_df.to_excel(writer, sheet_name='ALT. SOLUTIONS', index=False, header=False)
        else:
            # Create DataFrame with variables that have zero reduced cost
            alt_solutions_data = ['Variables with zero reduced cost'] + zero_reduced_cost_vars
            alt_solutions_df = pd.DataFrame(alt_solutions_data, columns=['Alternative Solutions Analysis'])
            alt_solutions_df.to_excel(writer, sheet_name='ALT. SOLUTIONS', index=False, header=False)

        # DMFA INPUTS sheet
        demand_alloys_df.to_excel(writer, sheet_name='DMFA INPUTS', index=False, startrow=0)
        forming_scrap_df.to_excel(writer, sheet_name='DMFA INPUTS', index=True, startrow=len(demand_alloys_df) + 2)
        fabrication_scrap_df.to_excel(writer, sheet_name='DMFA INPUTS', index=True, startrow=len(demand_alloys_df) + len(forming_scrap_df) + 4)
        eol_scrap_df.to_excel(writer, sheet_name='DMFA INPUTS', index=True, startrow=len(demand_alloys_df) + len(forming_scrap_df) + len(fabrication_scrap_df) + 6)

        # INPUT SCRAP COMPS sheet
        scrap_comp_df.to_excel(writer, sheet_name='INPUT SCRAP COMPS')

    print(f"Results and data exported to {filepath}")

    return

if __name__ == '__main__':

    # Specify year and scenario for analysis
    year = 2050
    scenario = "Baseline"

    # Set output folder path
    folder = r'RESULTS/' + scenario + '/'
    # Create folder if it doesn't exist
    os.makedirs(folder, exist_ok=True)

    model = optimize("primary", year, scenario)

    output = result_calculations(model, year, scenario)

    # OPTIONAL: Can generate output snapshot with results tables, ranked constraints, and alternative solutions analysis
    # generate_snapshot(model, year, scenario, folder)