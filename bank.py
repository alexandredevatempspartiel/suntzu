import discord
from discord.ext import commands, tasks
import os
import json
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re
import asyncio
import aiohttp
import time
import math

# Load environment variables from .env file
load_dotenv()

# Get Discord token and API key from environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
PNW_API_KEY = os.getenv('PNW_API_KEY')

# Base URL for Politics and War API
PNW_API_BASE_URL = "https://api.politicsandwar.com/graphql"

# File to store bank and member resources history data
BANK_HISTORY_FILE = "bank_history.json"
MARKET_PRICES_CACHE_FILE = "market_prices_cache.json"

# Fichier pour stocker l'historique des taxes
TAX_HISTORY_FILE = "tax_history.json"

class PnwApiLimiter:
    """
    Rate limiter for Politics & War API requests.
    Ensures requests don't exceed the rate limit (typically 60 requests per minute).
    """
    def __init__(self, requests_per_minute=60):
        self.requests_per_minute = requests_per_minute
        self.request_times = []
        self.lock = asyncio.Lock()
    
    async def execute_request(self, session, url, payload, headers):
        """
        Execute an API request while respecting rate limits.
        
        Args:
            session: aiohttp ClientSession
            url: API endpoint URL
            payload: Request payload
            headers: Request headers
            
        Returns:
            dict: Response data from API
        """
        async with self.lock:
            # Remove request timestamps older than 60 seconds
            current_time = time.time()
            self.request_times = [t for t in self.request_times if current_time - t < 60]
            
            # If we've reached the rate limit, wait until we can make another request
            if len(self.request_times) >= self.requests_per_minute:
                # Calculate how long to wait
                oldest_request = min(self.request_times)
                wait_time = 60 - (current_time - oldest_request) + 0.1  # Add 0.1s buffer
                
                if wait_time > 0:
                    print(f"Rate limit reached. Waiting {wait_time:.2f} seconds before next request.")
                    await asyncio.sleep(wait_time)
            
            # Make the request and record the timestamp
            self.request_times.append(time.time())
            
            try:
                async with session.post(url, json=payload, headers=headers, timeout=30) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"API request failed with status code {response.status}")
                        return None
            except Exception as e:
                print(f"Error during API request: {e}")
                return None

pnw_api_limiter = PnwApiLimiter()

# Default resource prices if API is unavailable
DEFAULT_PRICES = {
    'food': 50,
    'coal': 100,
    'oil': 150,
    'uranium': 400,
    'lead': 100,
    'iron': 100,
    'bauxite': 100,
    'gasoline': 175,
    'munitions': 250,
    'steel': 200,
    'aluminum': 150
}

# Configure Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    """Event triggered when the bot is ready."""
    print(f'{bot.user} has connected to Discord!')
    print(f'Monitoring for $get_bank commands and TARS responses...')
    print(f'Use !scan_history to scan previous messages for $get_bank commands')
    hourly_member_resources_collection.start()

async def get_pnw_market_prices():
    """
    Fetches current market prices from the Politics & War GraphQL API (v3).
    Returns a dictionary of resource prices.
    """
    if os.path.exists(MARKET_PRICES_CACHE_FILE):
        try:
            with open(MARKET_PRICES_CACHE_FILE, 'r') as f:
                cache_data = json.load(f)
                cache_time = cache_data.get('timestamp', 0)
                if time.time() - cache_time < 7200:
                    print("Using cached market prices")
                    return cache_data.get('prices', DEFAULT_PRICES)
        except (json.JSONDecodeError, KeyError):
            print("Cache file corrupted, will fetch new prices")
    
    if not PNW_API_KEY:
        print("No Politics & War API key provided, using default prices")
        return DEFAULT_PRICES
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.politicsandwar.com/graphql?api_key={PNW_API_KEY}"
            query = """
            query {
              tradeprices {
                data {
                  food
                  coal
                  oil
                  uranium
                  lead
                  iron
                  bauxite
                  gasoline
                  munitions
                  steel
                  aluminum
                }
              }
            }
            """
            async with session.post(url, json={"query": query}, timeout=10) as response:
                if response.status != 200:
                    print(f"API returned status {response.status}, using default prices")
                    return DEFAULT_PRICES
                data = await response.json()
                if 'data' in data and 'tradeprices' in data['data'] and data['data']['tradeprices']['data']:
                    trade_data = data['data']['tradeprices']['data'][0]
                    prices = DEFAULT_PRICES.copy()
                    for resource in DEFAULT_PRICES.keys():
                        if resource in trade_data and trade_data[resource] is not None:
                            prices[resource] = float(trade_data[resource])
                    cache_data = {'timestamp': time.time(), 'prices': prices}
                    with open(MARKET_PRICES_CACHE_FILE, 'w') as f:
                        json.dump(cache_data, f)
                    print("Successfully fetched market prices from GraphQL API")
                    return prices
                print("GraphQL API request unsuccessful, using default prices")
                return DEFAULT_PRICES
    except Exception as e:
        print(f"Error fetching market prices: {e}")
        return DEFAULT_PRICES



async def fetch_nation_data(nation_id):
    """
    Récupère les données détaillées d'une nation, y compris les villes et projets.
    
    Args:
        nation_id (int): L'ID de la nation
        
    Returns:
        dict: Données détaillées de la nation
    """
    query = """
    query {
        nations(first: 1, id: [""" + str(nation_id) + """]) {
            data {
                id
                nation_name
                alliance_id
                discord
                num_cities
                flag
                color
                score
                continent
                domestic_policy
                
                # Resources
                food
                uranium
                oil
                iron
                coal
                bauxite
                lead
                aluminum
                gasoline
                munitions
                steel
                money
                
                # Military
                soldiers
                tanks
                aircraft
                ships
                spies
                missiles
                nukes
                offensive_wars_count
                defensive_wars_count
                
                # Projects Manufacturing
                bauxite_works
                emergency_gasoline_reserve
                iron_works
                uranium_enrichment_program
                specialized_police_training_program
                international_trade_center
                telecommunications_satellite
                clinical_research_center
                green_technologies
                arms_stockpile
                
                # Projects Others
                government_support_agency
                mass_irrigation
                fallout_shelter
                bureau_of_domestic_affairs
                recycling_initiative
                
                cities {
                    date
                    infrastructure
                    land
                    
                    # Manufacturing
                    oil_refinery
                    steel_mill
                    aluminum_refinery
                    munitions_factory
                    
                    # Mines
                    uranium_mine
                    oil_well
                    iron_mine
                    coal_mine
                    bauxite_mine
                    lead_mine
                    farm
                    
                    # Commerce
                    subway
                    stadium
                    shopping_mall
                    bank
                    supermarket
                    police_station
                    hospital
                    recycling_center
                    
                    # Power
                    nuclear_power
                    coal_power
                    oil_power
                    wind_power
                }
            }
        }
    }
    """
    payload = {"query": query}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if (data.get("data") and data["data"].get("nations") and 
                    data["data"]["nations"].get("data") and len(data["data"]["nations"]["data"]) > 0):
                    return data["data"]["nations"]["data"][0]
                else:
                    print(f"Error: Nation data not found - {data}")
                    return None
            else:
                print(f"Error: Failed to fetch nation data - Status {response.status}")
                return None

async def fetch_game_info():
    """
    Récupère les informations générales du jeu (date, radiation).
    
    Returns:
        dict: Informations de jeu
    """
    query = """
    query {
        game_info {
            game_date
            radiation {
                global
                north_america
                south_america
                europe
                africa
                asia
                australia
                antarctica
            }
        }
    }
    """
    payload = {"query": query}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("data") and data["data"].get("game_info"):
                    return data["data"]["game_info"]
                else:
                    print(f"Error: Game info not found - {data}")
                    return None
            else:
                print(f"Error: Failed to fetch game info - Status {response.status}")
                return None

async def fetch_color_data():
    """
    Récupère les informations sur les bonus des couleurs.
    
    Returns:
        dict: Dictionnaire des bonus de couleur
    """
    query = """
    query {
        colors {
            color
            turn_bonus
        }
    }
    """
    payload = {"query": query}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if data.get("data") and data["data"].get("colors"):
                    colors = data["data"]["colors"]
                    return {color["color"]: color["turn_bonus"] for color in colors}
                else:
                    print(f"Error: Color data not found - {data}")
                    return {}
            else:
                print(f"Error: Failed to fetch color data - Status {response.status}")
                return {}

async def calculate_nation_revenue(nation_id):
    """
    Calcule le revenu journalier d'une nation.
    
    Args:
        nation_id (int): L'ID de la nation
        
    Returns:
        dict: Détails du revenu journalier
    """
    # Récupérer les données nécessaires
    nation = await fetch_nation_data(nation_id)
    if not nation:
        return {"error": "Nation non trouvée"}
    
    game_info = await fetch_game_info()
    if not game_info:
        return {"error": "Impossible de récupérer les informations du jeu"}
    
    color_bonuses = await fetch_color_data()
    
    # Récupérer les prix du marché (fonction déjà existante)
    market_prices = await get_pnw_market_prices()
    
    # Récupérer la couleur de l'alliance si la nation est dans une alliance
    alliance_color = None
    if nation.get("alliance_id"):
        alliance_query = """
        query {
            alliances(first: 1, id: [""" + str(nation["alliance_id"]) + """]) {
                data {
                    color
                }
            }
        }
        """
        payload = {"query": alliance_query}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if (data.get("data") and data["data"].get("alliances") and 
                        data["data"]["alliances"].get("data") and len(data["data"]["alliances"]["data"]) > 0):
                        alliance_color = data["data"]["alliances"]["data"][0]["color"]
    
    # Initialiser les variables pour les calculs
    production = {
        "food": 0,
        "uranium": 0,
        "oil": 0,
        "iron": 0,
        "coal": 0,
        "bauxite": 0,
        "lead": 0,
        "aluminum": 0, 
        "gasoline": 0,
        "munitions": 0,
        "steel": 0
    }
    
    income = {
        "gross_income": 0,
        "military_upkeep": 0,
        "improvement_upkeep": 0
    }
    
    # Date actuelle
    today = datetime.now()
    truncated_date = datetime(today.year, today.month, today.day)
    
    # Configuration des radiations par continent
    radiation = {
        'na': game_info["radiation"]["north_america"],
        'sa': game_info["radiation"]["south_america"],
        'as': game_info["radiation"]["asia"],
        'an': game_info["radiation"]["antarctica"],
        'eu': game_info["radiation"]["europe"],
        'af': game_info["radiation"]["africa"],
        'au': game_info["radiation"]["australia"]
    }
    continent_radiation = radiation.get(nation["continent"], 0)
    
    # Calculer la production et les revenus pour chaque ville
    for city in nation["cities"]:
        # Calcul de l'âge de la ville
        city_date = datetime.fromisoformat(city["date"].replace('Z', '+00:00'))
        age_days = max(1, (truncated_date - city_date).days)
        ln_age = math.log(age_days)
        city_age_modifier = 1 + max(ln_age / 15, 0)
        
        # Population de base
        base_population = city["infrastructure"] * 100
        
        # Consommation de nourriture par la population
        population_formula = ((base_population * base_population) / 125000000) + ((base_population * city_age_modifier - base_population) / 850)
        production["food"] -= population_formula
        
        # ----- PRODUCTION DE NOURRITURE -----
        # Bonus de spécialisation des améliorations
        improvement_specialization_bonus = 1 + ((0.5 * (city["farm"] - 1)) / (20 - 1))
        
        # Taux de production alimentaire (modifié par projet)
        if nation["mass_irrigation"]:
            food_production_rate = city["land"] / 400
        else:
            food_production_rate = city["land"] / 500
        
        # Modificateur de continent pour l'Antarctique
        if nation["continent"] == 'an':
            food_production_rate *= 0.5
        
        # Production alimentaire de base
        base_food_production = round(city["farm"] * improvement_specialization_bonus * food_production_rate * 100) / 100
        
        # Modificateur saisonnier
        hemisphere = 'Southern' if nation["continent"] in ['sa', 'as', 'af'] else 'Northern'
        seasonal_modifier = 1
        
        # Obtenir le mois de la date du jeu
        game_date = datetime.fromisoformat(game_info["game_date"].replace('Z', '+00:00'))
        game_date_month = game_date.month
        
        # Appliquer le modificateur saisonnier (sauf en Antarctique)
        if nation["continent"] != 'an':
            if hemisphere == 'Northern':
                if game_date_month in [12, 1, 2]:  # Hiver dans l'hémisphère nord
                    seasonal_modifier = 0.8
                elif game_date_month in [6, 7, 8]:  # Été dans l'hémisphère nord
                    seasonal_modifier = 1.2
            else:  # Hémisphère sud
                if game_date_month in [12, 1, 2]:  # Été dans l'hémisphère sud
                    seasonal_modifier = 1.2
                elif game_date_month in [6, 7, 8]:  # Hiver dans l'hémisphère sud
                    seasonal_modifier = 0.8
        
        # Pénalité de radiation
        radiation_index = float(continent_radiation) + float(game_info["radiation"]["global"])
        fallout_shelter_multiplier = 0.85 if nation["fallout_shelter"] else 1
        radiation_penalty = (radiation_index / (-1000)) * fallout_shelter_multiplier
        
        # Production alimentaire finale
        food_production = max(base_food_production * seasonal_modifier * (1 + radiation_penalty), 0)
        production["food"] += food_production * 12  # Multiplie par 12 pour obtenir la valeur journalière
        
        # ----- CONSOMMATION DES CENTRALES ÉLECTRIQUES -----
        # Centrale nucléaire
        current_infrastructure = (city["infrastructure"] + 999) // 1000  # Math.ceil(infrastructure / 1000)
        infrastructure_can_power = city["nuclear_power"] * 2000 // 1000
        smallest_two = min(current_infrastructure, infrastructure_can_power)
        production["uranium"] -= smallest_two * 2.4
        
        # Centrale à charbon
        current_infrastructure = (city["infrastructure"] + 99) // 100  # Math.ceil(infrastructure / 100)
        infrastructure_can_power = city["coal_power"] * 500 // 100
        smallest_two = min(current_infrastructure, infrastructure_can_power)
        production["coal"] -= smallest_two * 1.2
        
        # Centrale à pétrole
        infrastructure_can_power = city["oil_power"] * 500 // 100
        smallest_two = min(current_infrastructure, infrastructure_can_power)
        production["oil"] -= smallest_two * 1.2
        
        # ----- MULTIPLICATEURS DE PROJETS -----
        multipliers = {
            "oil": 1,
            "steel": 1,
            "uranium": 1,
            "aluminum": 1,
            "munitions": 1
        }
        
        commerce = 0
        commerce_cap = 100
        commerce_addition = 0
        
        # Appliquer les bonus de projets sur les productions
        if nation["bauxite_works"]:
            multipliers["aluminum"] = 1.36
        if nation["emergency_gasoline_reserve"]:
            multipliers["oil"] = 2
        if nation["iron_works"]:
            multipliers["steel"] = 1.36
        if nation["arms_stockpile"]:
            multipliers["munitions"] = 1.20
        if nation["uranium_enrichment_program"]:
            multipliers["uranium"] = 2
        
        # ----- CONSOMMATION DES USINES -----
        # Raffinerie de pétrole
        production["oil"] -= (3 * city["oil_refinery"]) * (1 + (0.5 * (city["oil_refinery"] - 1)) / (5 - 1)) * multipliers["oil"]
        
        # Aciérie
        production["iron"] -= (3 * city["steel_mill"]) * (1 + (0.5 * (city["steel_mill"] - 1)) / (5 - 1)) * multipliers["steel"]
        production["coal"] -= (3 * city["steel_mill"]) * (1 + (0.5 * (city["steel_mill"] - 1)) / (5 - 1)) * multipliers["steel"]
        
        # Raffinerie d'aluminium
        production["bauxite"] -= (3 * city["aluminum_refinery"]) * (1 + (0.5 * (city["aluminum_refinery"] - 1)) / (5 - 1)) * multipliers["aluminum"]
        
        # Usine de munitions
        production["lead"] -= (6 * city["munitions_factory"]) * (1 + (0.5 * (city["munitions_factory"] - 1)) / (5 - 1))
        
        # ----- PRODUCTION DES USINES -----
        # Raffinerie de pétrole produit de l'essence
        production["gasoline"] += (6 * city["oil_refinery"]) * (1 + (0.5 * (city["oil_refinery"] - 1)) / (5 - 1)) * multipliers["oil"]
        
        # Aciérie produit de l'acier
        production["steel"] += (9 * city["steel_mill"]) * (1 + (0.5 * (city["steel_mill"] - 1)) / (5 - 1)) * multipliers["steel"]
        
        # Raffinerie d'aluminium produit de l'aluminium
        production["aluminum"] += (9 * city["aluminum_refinery"]) * (1 + (0.5 * (city["aluminum_refinery"] - 1)) / (5 - 1)) * multipliers["aluminum"]
        
        # Usine de munitions produit des munitions
        production["munitions"] += (18 * city["munitions_factory"]) * (1 + (0.5 * (city["munitions_factory"] - 1)) / (5 - 1)) * multipliers["munitions"]
        
        # ----- PRODUCTION DES MINES -----
        # Mine d'uranium
        production["uranium"] += (city["uranium_mine"] * 3) * (1 + (0.5 * (city["uranium_mine"] - 1)) / (5 - 1)) * multipliers["uranium"]
        
        # Puits de pétrole
        production["oil"] += (city["oil_well"] * 3) * (1 + (0.5 * (city["oil_well"] - 1)) / (5 - 1))
        
        # Mine de fer
        production["iron"] += (city["iron_mine"] * 3) * (1 + (0.5 * (city["iron_mine"] - 1)) / (5 - 1))
        
        # Mine de charbon
        production["coal"] += (city["coal_mine"] * 3) * (1 + (0.5 * (city["coal_mine"] - 1)) / (5 - 1))
        
        # Mine de bauxite
        production["bauxite"] += (city["bauxite_mine"] * 3) * (1 + (0.5 * (city["bauxite_mine"] - 1)) / (5 - 1))
        
        # Mine de plomb
        production["lead"] += (city["lead_mine"] * 3) * (1 + (0.5 * (city["lead_mine"] - 1)) / (5 - 1))
        
        # ----- COMMERCE ET PROJETS -----
        # Projets affectant le commerce
        if nation["international_trade_center"]:
            if nation["telecommunications_satellite"]:
                commerce_cap = 125
            else:
                commerce_cap = 115
        
        if nation["international_trade_center"]:
            commerce_addition += 1
        if nation["specialized_police_training_program"]:
            commerce_addition += 4
        if nation["telecommunications_satellite"]:
            commerce_addition += 2
        
        # Densité de population
        population_density = base_population / city["land"] if city["land"] > 0 else 0
        
        # Calcul du commerce
        commerce += min(
            city["supermarket"]*4 + city["bank"]*6 + city["shopping_mall"]*8 + 
            city["stadium"]*10 + city["subway"]*8 + commerce_addition, 
            commerce_cap
        )
        
        # ----- MODIFICATEURS DE POLICE ET D'HÔPITAL -----
        police_modifier_modifier = 3.5 if nation["specialized_police_training_program"] else 2.5
        hospital_modifier_modifier = 3.5 if nation["clinical_research_center"] else 2.5
        
        # ----- MODIFICATEURS ENVIRONNEMENTAUX -----
        # Green Technologies
        farm_greentech_multiplier = 0.5 if nation["green_technologies"] else 1
        manu_greentech_multiplier = 0.75 if nation["green_technologies"] else 1
        subway_greentech_addition = 25 if nation["green_technologies"] else 0
        resource_production_upkeep_multiplier = 0.9 if nation["green_technologies"] else 1
        
        # ----- COÛTS D'ENTRETIEN DES INFRASTRUCTURES -----
        income["improvement_upkeep"] += (
            (city["coal_power"] * 1200) + 
            (city["oil_power"] * 1800) + 
            (city["nuclear_power"] * 10500) + 
            (city["wind_power"] * 500) + 
            (city["coal_mine"] * 400 * resource_production_upkeep_multiplier) + 
            (city["iron_mine"] * 1600 * resource_production_upkeep_multiplier) + 
            (city["lead_mine"] * 1500 * resource_production_upkeep_multiplier) + 
            (city["farm"] * 300 * resource_production_upkeep_multiplier) + 
            (city["oil_well"] * 600 * resource_production_upkeep_multiplier) + 
            (city["bauxite_mine"] * 1600 * resource_production_upkeep_multiplier) + 
            (city["uranium_mine"] * 5000 * resource_production_upkeep_multiplier) + 
            (city["oil_refinery"] * 4000 * resource_production_upkeep_multiplier) + 
            (city["steel_mill"] * 4000 * resource_production_upkeep_multiplier) + 
            (city["aluminum_refinery"] * 2500 * resource_production_upkeep_multiplier) + 
            (city["munitions_factory"] * 3500 * resource_production_upkeep_multiplier) + 
            (city["police_station"] * 750) + 
            (city["hospital"] * 1000) + 
            (city["recycling_center"] * 2500) + 
            (city["subway"] * 3250) + 
            (city["supermarket"] * 600) + 
            (city["bank"] * 1800) + 
            (city["shopping_mall"] * 5400) + 
            (city["stadium"] * 12150)
        )
        
        # ----- CALCUL DE LA POLLUTION -----
        pollution_index = (
            (city["coal_power"] * 8) + 
            (city["oil_power"] * 6) + 
            (city["bauxite_mine"] * 12) + 
            (city["coal_mine"] * 12) + 
            (city["farm"] * 2 * farm_greentech_multiplier) + 
            (city["iron_mine"] * 12) + 
            (city["lead_mine"] * 12) + 
            (city["oil_well"] * 12) + 
            (city["uranium_mine"] * 20) + 
            (city["oil_refinery"] * 32 * manu_greentech_multiplier) + 
            (city["steel_mill"] * 40 * manu_greentech_multiplier) + 
            (city["aluminum_refinery"] * 40 * manu_greentech_multiplier) + 
            (city["munitions_factory"] * 32 * manu_greentech_multiplier) + 
            (city["police_station"] * 1) + 
            (city["hospital"] * 4) - 
            (city["recycling_center"] * (75 if nation["recycling_initiative"] else 70)) - 
            (city["subway"] * (45 + subway_greentech_addition)) + 
            (city["shopping_mall"] * 2) + 
            (city["stadium"] * 5)
        )
        
        # Calcul des modificateurs finaux
        police_modifier = city["police_station"] * police_modifier_modifier
        pollution_modifier = pollution_index * 0.05
        hospital_modifier = city["hospital"] * hospital_modifier_modifier
        
        # ----- TAUX DE CRIMINALITÉ ET DE MALADIE -----
        crime_rate = (((103 - commerce)*(103 - commerce)) + (city["infrastructure"] * 100))/(111111) - police_modifier
        disease_rate = ((((population_density*population_density) * 0.01) - 25)/100) + (base_population/100000) + pollution_modifier - hospital_modifier
        
        # ----- POPULATION FINALE -----
        population = base_population - (max(((disease_rate * 100 * city["infrastructure"])/100), 0)) - (max((crime_rate / 10) * (100*city["infrastructure"]) - 25, 0))
        population = population * (1 + ln_age/15)
        
        # ----- CALCUL DU REVENU -----
        income["gross_income"] += ((((commerce / 50) * 0.725) + 0.725) * population)
    
    # ----- CALCUL DES COÛTS MILITAIRES -----
    # Formule de consommation de nourriture par les soldats
    if nation["offensive_wars_count"] + nation["defensive_wars_count"] == 0:  # Temps de paix
        soldier_formula = nation["soldiers"] / 750
        income["military_upkeep"] += (nation["soldiers"] * 1.25) + (nation["tanks"] * 50) + (nation["aircraft"] * 500) + (nation["ships"] * 3375) + (nation["missiles"] * 21000) + (nation["nukes"] * 35000)
    else:  # Temps de guerre
        soldier_formula = nation["soldiers"] / 500
        income["military_upkeep"] += (nation["soldiers"] * 1.88) + (nation["tanks"] * 75) + (nation["aircraft"] * 750) + (nation["ships"] * 5062.50) + (nation["missiles"] * 31500) + (nation["nukes"] * 52500)
    
    # Coût des espions
    income["military_upkeep"] += nation["spies"] * 2400
    
    # Consommation de nourriture par les soldats
    production["food"] -= soldier_formula
    
    # ----- POLITIQUE INTÉRIEURE -----
    domestic_policy_increase = 1
    if nation["government_support_agency"]:
        domestic_policy_increase += 0.5
        if nation["bureau_of_domestic_affairs"]:
            domestic_policy_increase += 0.25
    
    if nation["domestic_policy"] == 'OPEN_MARKETS':
        income["gross_income"] = income["gross_income"] * (1 + (0.01 * domestic_policy_increase))
    elif nation["domestic_policy"] == 'IMPERIALISM':
        income["military_upkeep"] = income["military_upkeep"] * (1 - (0.05 * domestic_policy_increase))
    
    # ----- BONUS DE COULEUR -----
    color_bonus = 0
    if nation["color"] == alliance_color:
        color_bonus = color_bonuses.get(alliance_color, 0)
    elif nation["color"] == "beige":
        color_bonus = color_bonuses.get("beige", 0)
    color_bonus *= 12  # Multiplie par 12 pour obtenir la valeur journalière
    
    # ----- CALCUL DE LA VALEUR MONÉTAIRE DES RESSOURCES -----
    total_monetary_value = 0
    monetary_resources = {}
    
    for resource, amount in production.items():
        price = float(market_prices.get(resource, 0))
        monetary_value = amount * price
        total_monetary_value += monetary_value
        monetary_resources[resource] = {
            "amount": amount,
            "value": monetary_value
        }
    
    # ----- CALCUL DU REVENU NET -----
    gross_income = color_bonus + income["gross_income"]
    gross_upkeep = income["improvement_upkeep"] + income["military_upkeep"]
    net_income = gross_income - gross_upkeep
    monetary_net_income = net_income + total_monetary_value
    
    # Résultat
    return {
        "nation_name": nation["nation_name"],
        "flag": nation["flag"],
        "num_cities": nation["num_cities"],
        "production": production,
        "monetary_resources": monetary_resources,
        "total_monetary_value": total_monetary_value,
        "income": {
            "gross_income": income["gross_income"],
            "color_bonus": color_bonus,
            "gross_total": gross_income,
            "military_upkeep": income["military_upkeep"],
            "improvement_upkeep": income["improvement_upkeep"],
            "gross_upkeep": gross_upkeep,
            "net_income": net_income,
            "monetary_net_income": monetary_net_income
        }
    }

async def calculate_alliance_revenue(alliance_id):
    """
    Calcule le revenu journalier de toute une alliance.
    
    Args:
        alliance_id (int): L'ID de l'alliance
        
    Returns:
        dict: Détails du revenu journalier de l'alliance
    """
    # Récupérer les données de l'alliance
    alliance_data = await fetch_alliance_data(alliance_id)
    if not alliance_data:
        return {"error": "Alliance non trouvée"}
    
    # Récupérer les informations de jeu et les prix du marché (une seule fois pour tous les membres)
    game_info = await fetch_game_info()
    color_bonuses = await fetch_color_data()
    market_prices = await get_pnw_market_prices()
    
    if not game_info:
        return {"error": "Impossible de récupérer les informations du jeu"}
    
    # Filtrer pour ne prendre que les membres (pas les candidats)
    member_nations = [nation for nation in alliance_data["nations"] if nation["alliance_position"] != "APPLICANT"]
    
    # Initialiser les variables pour agréger les résultats
    alliance_production = {
        "food": 0, "uranium": 0, "oil": 0, "iron": 0, "coal": 0,
        "bauxite": 0, "lead": 0, "aluminum": 0, "gasoline": 0,
        "munitions": 0, "steel": 0
    }
    
    alliance_income = {
        "gross_income": 0,
        "color_bonus": 0,
        "gross_total": 0,
        "military_upkeep": 0,
        "improvement_upkeep": 0,
        "gross_upkeep": 0,
        "net_income": 0,
        "monetary_net_income": 0
    }
    
    alliance_total_monetary_value = 0
    alliance_monetary_resources = {resource: {"amount": 0, "value": 0} for resource in alliance_production}
    
    total_cities = 0
    processed_nations = 0
    failed_nations = 0
    alliance_color = alliance_data.get("color", "")  # Couleur de l'alliance pour le bonus
    
    # Liste pour stocker les résultats de nations individuelles
    member_results = []
    
    # Traitement de chaque nation
    for nation in member_nations:
        try:
            nation_id = nation["id"]
            
            # Calculer le revenu de cette nation
            nation_revenue = await calculate_nation_revenue(nation_id)
            
            # Si une erreur est survenue, passer à la nation suivante
            if "error" in nation_revenue:
                failed_nations += 1
                continue
            
            # Ajouter les résultats au total de l'alliance
            for resource in alliance_production:
                alliance_production[resource] += nation_revenue["production"].get(resource, 0)
                if resource in nation_revenue["monetary_resources"]:
                    alliance_monetary_resources[resource]["amount"] += nation_revenue["monetary_resources"][resource]["amount"]
                    alliance_monetary_resources[resource]["value"] += nation_revenue["monetary_resources"][resource]["value"]
            
            alliance_total_monetary_value += nation_revenue["total_monetary_value"]
            
            alliance_income["gross_income"] += nation_revenue["income"]["gross_income"]
            alliance_income["color_bonus"] += nation_revenue["income"]["color_bonus"]
            alliance_income["gross_total"] += nation_revenue["income"]["gross_total"]
            alliance_income["military_upkeep"] += nation_revenue["income"]["military_upkeep"]
            alliance_income["improvement_upkeep"] += nation_revenue["income"]["improvement_upkeep"]
            alliance_income["gross_upkeep"] += nation_revenue["income"]["gross_upkeep"]
            alliance_income["net_income"] += nation_revenue["income"]["net_income"]
            alliance_income["monetary_net_income"] += nation_revenue["income"]["monetary_net_income"]
            
            total_cities += nation_revenue["num_cities"]
            processed_nations += 1
            
            # Conserver les résultats individuels pour l'affichage
            member_results.append({
                "id": nation_id,
                "name": nation_revenue["nation_name"],
                "cities": nation_revenue["num_cities"],
                "net_income": nation_revenue["income"]["net_income"],
                "total_income": nation_revenue["income"]["monetary_net_income"]
            })
            
            # Pause pour éviter de surcharger l'API
            await asyncio.sleep(5)
            
        except Exception as e:
            print(f"Erreur lors du traitement de la nation ID {nation['id']}: {e}")
            failed_nations += 1
    
    # Trier les nations par revenu total
    member_results.sort(key=lambda x: x["total_income"], reverse=True)
    
    # Résultat
    return {
        "alliance_name": alliance_data.get("name", "Alliance inconnue"),
        "member_count": len(member_nations),
        "processed_nations": processed_nations,
        "failed_nations": failed_nations,
        "total_cities": total_cities,
        "production": alliance_production,
        "monetary_resources": alliance_monetary_resources,
        "total_monetary_value": alliance_total_monetary_value,
        "income": alliance_income,
        "member_results": member_results
    }

def load_bank_history():
    """Load the bank and member resources history data from file."""
    if os.path.exists(BANK_HISTORY_FILE):
        try:
            with open(BANK_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error: Could not decode {BANK_HISTORY_FILE}, creating new history data")
            return {"alliance_id": None, "bank_records": [], "member_resources_records": []}
    return {"alliance_id": None, "bank_records": [], "member_resources_records": []}

def save_bank_history(history_data):
    """Save the bank and member resources history data to file."""
    with open(BANK_HISTORY_FILE, 'w') as f:
        json.dump(history_data, f, indent=2)

async def fetch_alliance_data(alliance_id):
    """
    Fetch comprehensive data about an alliance including members.
    
    Args:
        alliance_id (int): The ID of the alliance
        
    Returns:
        dict: Alliance data including members
    """
    query = """
    query {
        alliances(first: 1, id: [""" + str(alliance_id) + """]) {
            data {
                id
                name
                nations {
                    id
                    nation_name
                    alliance_position
                }
            }
        }
    }
    """
    payload = {"query": query}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if (data.get("data") and data["data"].get("alliances") and 
                    data["data"]["alliances"].get("data") and len(data["data"]["alliances"]["data"]) > 0):
                    return data["data"]["alliances"]["data"][0]
                else:
                    print(f"Error: Alliance data not found - {data}")
                    return None
            else:
                print(f"Error: Failed to fetch alliance data - Status {response.status}")
                return None

async def fetch_nation_resources(nation_id):
    """
    Fetch resource data for a specific nation.
    
    Args:
        nation_id (int): The ID of the nation
        
    Returns:
        dict: Resources data for each resource type
    """
    query = """
    query {
        nations(first: 1, id: [""" + str(nation_id) + """]) {
            data {
                id
                nation_name
                money
                coal
                oil
                uranium
                iron
                bauxite
                lead
                gasoline
                munitions
                steel
                aluminum
                food
            }
        }
    }
    """
    payload = {"query": query}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                if (data.get("data") and data["data"].get("nations") and 
                    data["data"]["nations"].get("data") and len(data["data"]["nations"]["data"]) > 0):
                    nation_data = data["data"]["nations"]["data"][0]
                    return {
                        "nation_name": nation_data["nation_name"],
                        "money": nation_data.get("money", 0),
                        "food": nation_data.get("food", 0),
                        "steel": nation_data.get("steel", 0),
                        "aluminum": nation_data.get("aluminum", 0),
                        "gasoline": nation_data.get("gasoline", 0),
                        "munitions": nation_data.get("munitions", 0),
                        "uranium": nation_data.get("uranium", 0),
                        "coal": nation_data.get("coal", 0),
                        "oil": nation_data.get("oil", 0),
                        "iron": nation_data.get("iron", 0),
                        "lead": nation_data.get("lead", 0),
                        "bauxite": nation_data.get("bauxite", 0),
                    }
                else:
                    print(f"Error: Nation data not found - {data}")
                    return None
            else:
                print(f"Error: Failed to fetch nation data - Status {response.status}")
                return None

async def fetch_and_aggregate_member_resources(alliance_id):
    """
    Fetch and aggregate resources for all members of an alliance.
    
    Args:
        alliance_id (int): The ID of the alliance
        
    Returns:
        dict: Aggregated resources and total value
    """
    alliance_data = await fetch_alliance_data(alliance_id)
    if not alliance_data:
        return None
    
    member_nations = [nation for nation in alliance_data["nations"] if nation["alliance_position"] != "APPLICANT"]
    member_nation_ids = [nation["id"] for nation in member_nations]
    
    total_resources = {
        "money": 0, "food": 0, "steel": 0, "aluminum": 0, "gasoline": 0,
        "munitions": 0, "uranium": 0, "coal": 0, "oil": 0, "iron": 0,
        "lead": 0, "bauxite": 0,
    }
    
    for nation_id in member_nation_ids:
        resources_data = await fetch_nation_resources(nation_id)
        if resources_data:
            for resource in total_resources.keys():
                if resource in resources_data:
                    total_resources[resource] += resources_data[resource]
        await asyncio.sleep(0.8)  # Avoid rate limiting
    
    market_prices = await get_pnw_market_prices()
    total_value = 0
    for resource, amount in total_resources.items():
        if resource == "money":
            total_value += amount
        else:
            total_value += amount * market_prices.get(resource, 0)
    
    return {"resources": total_resources, "total_value": total_value}

async def fetch_alliance_tax_records(alliance_id, days=14):
    """
    Récupère les enregistrements fiscaux d'une alliance sans utiliser de filtre.
    Filtre les résultats côté client en gérant correctement les fuseaux horaires.
    
    Args:
        alliance_id (int): ID de l'alliance
        days (int): Nombre de jours d'historique à récupérer (max 14 jours)
        
    Returns:
        dict: Données d'alliance avec liste des enregistrements fiscaux
    """
    # Requête sans filtre after
    query = """
    query {
        alliances(first: 1, id: [""" + str(alliance_id) + """]) {
            data {
                id
                name
                taxrecs(
                    orderBy: {column: DATE, order: DESC}
                ) {
                    id
                    date
                    sender_id
                    sender_type
                    receiver_id
                    receiver_type
                    note
                    money
                    coal
                    oil
                    uranium
                    iron
                    bauxite
                    lead
                    gasoline
                    munitions
                    steel
                    aluminum
                    food
                    tax_id
                }
            }
        }
    }
    """
    
    payload = {"query": query}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {PNW_API_KEY}"}
    
    print(f"Envoi requête simple sans filtre pour alliance_id={alliance_id}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(PNW_API_BASE_URL, json=payload, headers=headers) as response:
                print(f"Statut réponse API: {response.status}")
                if response.status == 200:
                    data = await response.json()
                    
                    if data and "errors" not in data and data.get("data") and data["data"].get("alliances") and data["data"]["alliances"].get("data"):
                        if len(data["data"]["alliances"]["data"]) > 0:
                            alliance_data = data["data"]["alliances"]["data"][0]
                            alliance_name = alliance_data.get("name", "Unknown Alliance")
                            print(f"Données alliance trouvées: {alliance_name}")
                            
                            if "taxrecs" in alliance_data and alliance_data["taxrecs"]:
                                tax_records = alliance_data["taxrecs"]
                                print(f"Nombre d'enregistrements fiscaux bruts: {len(tax_records)}")
                                
                                # Créer une date limite il y a 'days' jours
                                cutoff_date = datetime.now() - timedelta(days=days)
                                
                                filtered_records = []
                                for record in tax_records:
                                    if "date" in record:
                                        try:
                                            # Convertir la date de l'enregistrement en naive datetime
                                            record_date_str = record["date"].replace('Z', '').split('+')[0]
                                            record_date = datetime.fromisoformat(record_date_str)
                                            
                                            # Comparaison de deux naive datetimes
                                            if record_date >= cutoff_date:
                                                filtered_records.append(record)
                                        except ValueError as e:
                                            print(f"Erreur de parsing de date: {e} pour {record.get('date')}")
                                
                                print(f"Après filtrage: {len(filtered_records)} enregistrements dans les {days} derniers jours")
                                
                                return {
                                    "name": alliance_name,
                                    "tax_records": filtered_records
                                }
                            else:
                                print("Aucun enregistrement fiscal trouvé")
                        else:
                            print(f"Aucune alliance trouvée avec l'ID {alliance_id}")
                    else:
                        print(f"Erreurs dans la réponse API: {data.get('errors', 'Erreur inconnue')}")
                else:
                    print(f"Erreur API: {response.status}, contenu: {await response.text()}")
        except Exception as e:
            print(f"Exception lors de la requête API: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    
    return {"name": "Unknown Alliance", "tax_records": []}


def load_tax_history():
    """Charge l'historique des taxes depuis le fichier."""
    if os.path.exists(TAX_HISTORY_FILE):
        try:
            with open(TAX_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error: Could not decode {TAX_HISTORY_FILE}, creating new history data")
            return {"alliances": {}}
    return {"alliances": {}}

def save_tax_history(tax_history):
    """Enregistre l'historique des taxes dans le fichier."""
    with open(TAX_HISTORY_FILE, 'w') as f:
        json.dump(tax_history, f, indent=2)

async def process_alliance_tax_data(alliance_id, days=14):
    """
    Récupère, traite et stocke les données fiscales d'une alliance regroupées par jour.
    
    Args:
        alliance_id (int): ID de l'alliance
        days (int): Nombre de jours d'historique à récupérer
        
    Returns:
        dict: Données fiscales quotidiennes
    """
    # Récupérer les enregistrements fiscaux
    tax_data = await fetch_alliance_tax_records(alliance_id, days)
    alliance_name = tax_data["name"]
    tax_records = tax_data["tax_records"]
    
    # Si aucun enregistrement, retourner immédiatement
    if not tax_records:
        return {"alliance_name": alliance_name, "daily_records": []}
    
    # Obtenir les prix du marché pour calculer les valeurs
    market_prices = await get_pnw_market_prices()
    
    # Grouper les enregistrements par jour
    daily_records = {}
    
    for record in tax_records:
        if not record.get("tax_id"):
            continue  # Ignorer les enregistrements qui ne sont pas des taxes
        
        try:
            # Convertir la date au format YYYY-MM-DD
            record_date = datetime.fromisoformat(record["date"].replace('Z', '+00:00'))
            date_str = record_date.strftime("%Y-%m-%d")
            
            # Initialiser le dictionnaire pour ce jour s'il n'existe pas encore
            if date_str not in daily_records:
                daily_records[date_str] = {
                    "date": date_str,
                    "money": 0,
                    "coal": 0,
                    "oil": 0,
                    "uranium": 0,
                    "iron": 0,
                    "bauxite": 0,
                    "lead": 0,
                    "gasoline": 0,
                    "munitions": 0,
                    "steel": 0,
                    "aluminum": 0,
                    "food": 0,
                    "count": 0,
                    "total_value": 0
                }
            
            # Ajouter les ressources à ce jour
            daily_records[date_str]["count"] += 1
            for resource in ["money", "coal", "oil", "uranium", "iron", "bauxite", "lead", "gasoline", "munitions", "steel", "aluminum", "food"]:
                if resource in record and record[resource]:
                    daily_records[date_str][resource] += record[resource]
        
        except (ValueError, KeyError) as e:
            print(f"Erreur lors du traitement d'un enregistrement fiscal: {e}")
            continue
    
    # Calculer la valeur totale pour chaque jour
    for date_str, day_data in daily_records.items():
        total_value = day_data["money"]
        for resource, amount in day_data.items():
            if resource not in ["date", "money", "count", "total_value"] and amount > 0:
                total_value += amount * market_prices.get(resource, 0)
        day_data["total_value"] = total_value
    
    # Charger l'historique existant
    tax_history = load_tax_history()
    
    # S'assurer que l'entrée pour cette alliance existe
    if str(alliance_id) not in tax_history["alliances"]:
        tax_history["alliances"][str(alliance_id)] = {
            "name": alliance_name,
            "daily_records": {}
        }
    
    # Mettre à jour les enregistrements quotidiens
    for date_str, day_data in daily_records.items():
        tax_history["alliances"][str(alliance_id)]["daily_records"][date_str] = day_data
    
    # Enregistrer l'historique mis à jour
    save_tax_history(tax_history)
    
    # Convertir le dictionnaire en liste triée par date pour faciliter l'utilisation
    sorted_records = [v for k, v in sorted(daily_records.items())]
    
    return {
        "alliance_name": alliance_name,
        "daily_records": sorted_records
    }

@tasks.loop(hours=1)
async def hourly_member_resources_collection():
    """Task that runs every hour to collect and store member resources data."""
    try:
        history_data = load_bank_history()
        alliance_id = history_data.get("alliance_id")
        
        if not alliance_id:
            print("No alliance ID set for member resources tracking.")
            return
        
        print(f"Running hourly member resources collection for alliance ID {alliance_id}...")
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        aggregated_data = await fetch_and_aggregate_member_resources(alliance_id)
        
        if aggregated_data:
            record = {
                "timestamp": timestamp,
                "resources": aggregated_data["resources"],
                "total_value": aggregated_data["total_value"]
            }
            if "member_resources_records" not in history_data:
                history_data["member_resources_records"] = []
            history_data["member_resources_records"].append(record)
            save_bank_history(history_data)
            print(f"Collected member resources data for alliance ID {alliance_id} at {timestamp}")
        else:
            print(f"Could not collect member resources data for alliance ID {alliance_id}")
    except Exception as e:
        print(f"Error in hourly member resources collection: {e}")

@hourly_member_resources_collection.before_loop
async def before_hourly_member_resources_collection():
    """Wait until the bot is ready before starting the task."""
    await bot.wait_until_ready()
    print("Hourly member resources collection task initialized, waiting for first execution")

@bot.command(name='set_alliance_id')
async def set_alliance_id(ctx, alliance_id: int):
    """Set the alliance ID for member resources tracking."""
    history_data = load_bank_history()
    history_data["alliance_id"] = alliance_id
    save_bank_history(history_data)
    await ctx.send(f"✅ Alliance ID set to {alliance_id} for member resources tracking.")

@bot.command(name='member_resources_history')
async def show_member_resources_history(ctx, days: int = 7):
    """Display historical member resources data."""
    try:
        history_data = load_bank_history()
        if "member_resources_records" not in history_data or not history_data["member_resources_records"]:
            await ctx.send("❌ No member resources history data found. Wait for the hourly collection.")
            return
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        filtered_records = [r for r in history_data["member_resources_records"] if r["timestamp"].split()[0] >= cutoff_date]
        
        if not filtered_records:
            await ctx.send(f"❌ No member resources history data found within the last {days} days.")
            return
        
        embed = discord.Embed(
            title=f"Member Resources History (Last {days} days)",
            description=f"Showing {len(filtered_records)} data points",
            color=discord.Color.green()
        )
        newest = filtered_records[-1]
        oldest = filtered_records[0]
        embed.add_field(name="📊 Latest Data", value=f"Time: {newest['timestamp']}", inline=False)
        
        newest_value = newest["total_value"]
        oldest_value = oldest["total_value"]
        difference = newest_value - oldest_value
        percent_change = (difference / oldest_value) * 100 if oldest_value else 0
        
        embed.add_field(name="💰 Total Value", value=f"${newest_value:,.2f}", inline=True)
        embed.add_field(
            name=f"{'📈' if difference >= 0 else '📉'} Change",
            value=f"${difference:,.2f} ({percent_change:.2f}%)",
            inline=True
        )
        embed.set_footer(text=f"Use !member_resources_graph for visualization | Retrieved at {ctx.message.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in show_member_resources_history: {e}")

@bot.command(name='member_resources_graph')
async def generate_member_resources_graph(ctx, days: int = 30):
    """Generate and display a graph of member resources value over time."""
    try:
        history_data = load_bank_history()
        if "member_resources_records" not in history_data or not history_data["member_resources_records"]:
            await ctx.send("❌ No member resources history data found. Wait for the hourly collection.")
            return
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        filtered_records = [r for r in history_data["member_resources_records"] if r["timestamp"].split()[0] >= cutoff_date]
        
        if not filtered_records:
            await ctx.send(f"❌ No member resources history data found within the last {days} days.")
            return
        
        dates = [datetime.strptime(r["timestamp"], '%Y-%m-%d %H:%M:%S') for r in filtered_records]
        total_values = [r["total_value"] for r in filtered_records]
        df = pd.DataFrame({'date': dates, 'total_value': total_values}).sort_values('date')
        
        plt.figure(figsize=(10, 6))
        plt.plot(df['date'], df['total_value'], marker='o', linestyle='-', color='green')
        plt.title(f'Alliance Member Resources Value Over Time (Last {days} days)')
        plt.xlabel('Date')
        plt.ylabel('Total Value ($)')
        plt.grid(True, alpha=0.3)
        plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        temp_filename = 'member_resources_history_graph.png'
        plt.savefig(temp_filename)
        plt.close()
        
        await ctx.send(f"📊 Member resources value history graph (last {days} days):", file=discord.File(temp_filename))
        os.remove(temp_filename)
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in generate_member_resources_graph: {e}")

@bot.command(name='compare_bank_members')
async def compare_bank_members_graph(ctx, days: int = 30):
    """Generate a comparison graph of bank value and member resources value over time."""
    try:
        history_data = load_bank_history()
        if not history_data["bank_records"] or not history_data.get("member_resources_records"):
            await ctx.send("❌ Insufficient data for comparison. Ensure both bank and member resources data are available.")
            return
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        bank_records = [r for r in history_data["bank_records"] if r["timestamp"].split()[0] >= cutoff_date]
        member_records = [r for r in history_data.get("member_resources_records", []) if r["timestamp"].split()[0] >= cutoff_date]
        
        if not bank_records or not member_records:
            await ctx.send(f"❌ Insufficient data within the last {days} days for comparison.")
            return
        
        bank_dates = [datetime.strptime(r["timestamp"], '%Y-%m-%d %H:%M:%S') for r in bank_records]
        bank_values = [r["resources"]["total_value"] for r in bank_records]
        member_dates = [datetime.strptime(r["timestamp"], '%Y-%m-%d %H:%M:%S') for r in member_records]
        member_values = [r["total_value"] for r in member_records]
        
        plt.figure(figsize=(12, 6))
        plt.plot(bank_dates, bank_values, marker='o', linestyle='-', color='blue', label='Bank Value')
        plt.plot(member_dates, member_values, marker='o', linestyle='-', color='green', label='Member Resources Value')
        plt.title(f'Bank Value vs Member Resources Value (Last {days} days)')
        plt.xlabel('Date')
        plt.ylabel('Value ($)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        temp_filename = 'comparison_graph.png'
        plt.savefig(temp_filename)
        plt.close()
        
        await ctx.send(f"📊 Comparison graph (last {days} days):", file=discord.File(temp_filename))
        os.remove(temp_filename)
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in compare_bank_members_graph: {e}")

def load_bank_history():
    """Load the bank history data from file."""
    if os.path.exists(BANK_HISTORY_FILE):
        try:
            with open(BANK_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error: Could not decode {BANK_HISTORY_FILE}, creating new history data")
            return {"bank_records": []}
    return {"bank_records": []}

def is_valid_tars_response(message):
    """Determine if a message is a valid TARS response based on embed title and content."""
    if message.author.name != "TARS":
        return False
    if not message.embeds:  # No embed present
        return False
    embed = message.embeds[0]
    if not embed.title:  # No title in embed
        return False
    title_lower = embed.title.lower()
    content_lower = message.content.lower()
    # Must have "success" in title and no "emptying" in title or content
    if "success" not in title_lower or "emptying" in title_lower or "emptying" in content_lower:
        return False
    return True

def save_bank_history(history_data):
    """Save the bank history data to file."""
    with open(BANK_HISTORY_FILE, 'w') as f:
        json.dump(history_data, f, indent=2)

async def extract_resources_from_tars_response(message_or_content):
    """Extract resource values from TARS bot response.
    
    Args:
        message_or_content: Either a Discord message object or a string with the message content
    """
    resources = {}
    
    # Determine if we have a message object or just the content string
    if isinstance(message_or_content, str):
        content = message_or_content
        has_embeds = False
    else:
        content = message_or_content.content
        has_embeds = hasattr(message_or_content, 'embeds') and message_or_content.embeds
    
    # First try to extract from message content directly
    # Look for pattern like "$1,773,250,299.94" for total value
    money_match = re.search(r'Money\s+\$([0-9,]+\.[0-9]+)', content)
    if money_match:
        try:
            money = float(money_match.group(1).replace(',', ''))
            resources['money'] = money
        except ValueError:
            pass
            
    # Check for embeds if they exist
    if has_embeds and len(message_or_content.embeds) > 0:
        embed = message_or_content.embeds[0]
        
        # Extract from embed fields
        for field in embed.fields:
            # Process each field based on its name
            field_name = field.name.strip().lower() if field.name else ''
            field_value = field.value.strip() if field.value else ''
            
            if "money" in field_name:
                money_match = re.search(r'\$?([0-9,]+\.[0-9]+)', field_value)
                if money_match:
                    resources['money'] = float(money_match.group(1).replace(',', ''))
            elif "food" in field_name:
                food_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if food_match:
                    resources['food'] = float(food_match.group(1).replace(',', ''))
            elif "coal" in field_name:
                coal_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if coal_match:
                    resources['coal'] = float(coal_match.group(1).replace(',', ''))
            elif "oil" in field_name:
                oil_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if oil_match:
                    resources['oil'] = float(oil_match.group(1).replace(',', ''))
            elif "uranium" in field_name:
                uranium_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if uranium_match:
                    resources['uranium'] = float(uranium_match.group(1).replace(',', ''))
            elif "lead" in field_name:
                lead_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if lead_match:
                    resources['lead'] = float(lead_match.group(1).replace(',', ''))
            elif "iron" in field_name:
                iron_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if iron_match:
                    resources['iron'] = float(iron_match.group(1).replace(',', ''))
            elif "bauxite" in field_name:
                bauxite_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if bauxite_match:
                    resources['bauxite'] = float(bauxite_match.group(1).replace(',', ''))
            elif "gasoline" in field_name:
                gasoline_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if gasoline_match:
                    resources['gasoline'] = float(gasoline_match.group(1).replace(',', ''))
            elif "munitions" in field_name:
                munitions_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if munitions_match:
                    resources['munitions'] = float(munitions_match.group(1).replace(',', ''))
            elif "steel" in field_name:
                steel_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if steel_match:
                    resources['steel'] = float(steel_match.group(1).replace(',', ''))
            elif "aluminum" in field_name:
                aluminum_match = re.search(r'([0-9,]+\.[0-9]+)', field_value)
                if aluminum_match:
                    resources['aluminum'] = float(aluminum_match.group(1).replace(',', ''))
            elif "total value" in field_name:
                total_match = re.search(r'\$([0-9,]+\.[0-9]+)', field_value)
                if total_match:
                    resources['total_value'] = float(total_match.group(1).replace(',', ''))
            elif "loan" in field_name:
                loan_match = re.search(r'\$([0-9,]+)', field_value)
                if loan_match:
                    resources['loan'] = float(loan_match.group(1).replace(',', ''))
        
        # If we couldn't find total_value in fields, try to find it in the description or footer
        if 'total_value' not in resources and embed.description:
            total_match = re.search(r'Total Value\s+\$([0-9,]+\.[0-9]+)', embed.description)
            if total_match:
                resources['total_value'] = float(total_match.group(1).replace(',', ''))
        
        # Try footer text
        if 'total_value' not in resources and embed.footer:
            footer_text = embed.footer.text if embed.footer.text else ""
            total_match = re.search(r'Total Value\s+\$([0-9,]+\.[0-9]+)', footer_text)
            if total_match:
                resources['total_value'] = float(total_match.group(1).replace(',', ''))
                
    # If no data was found in embeds, or there are no embeds, try the regular content
    if not resources or len(resources) <= 1:
        # Extract money using regex
        money_match = re.search(r'Money\s+\$?([0-9,]+\.[0-9]+)', content)
        if money_match:
            resources['money'] = float(money_match.group(1).replace(',', ''))
        
        # Extract other resources
        patterns = {
            'food': r'Food\s+([0-9,]+\.[0-9]+)',
            'coal': r'Coal\s+([0-9,]+\.[0-9]+)',
            'oil': r'Oil\s+([0-9,]+\.[0-9]+)',
            'uranium': r'Uranium\s+([0-9,]+\.[0-9]+)',
            'lead': r'Lead\s+([0-9,]+\.[0-9]+)',
            'iron': r'Iron\s+([0-9,]+\.[0-9]+)',
            'bauxite': r'Bauxite\s+([0-9,]+\.[0-9]+)',
            'gasoline': r'Gasoline\s+([0-9,]+\.[0-9]+)',
            'munitions': r'Munitions\s+([0-9,]+\.[0-9]+)',
            'steel': r'Steel\s+([0-9,]+\.[0-9]+)',
            'aluminum': r'Aluminum\s+([0-9,]+\.[0-9]+)',
        }
        
        for resource, pattern in patterns.items():
            match = re.search(pattern, content)
            if match:
                resources[resource] = float(match.group(1).replace(',', ''))
        
        # Extract total value
        total_match = re.search(r'Total Value\s+\$([0-9,]+\.[0-9]+)', content)
        if total_match:
            resources['total_value'] = float(total_match.group(1).replace(',', ''))
    
    # Get current market prices
    market_prices = await get_pnw_market_prices()
    
    # Calculate total value based on resources and market prices
    if 'total_value' not in resources and resources:
        calculated_value = 0
        
        # Add money directly if available
        if 'money' in resources:
            calculated_value += resources['money']
        
        # Add value of each resource
        for resource, amount in resources.items():
            if resource != 'money' and resource != 'total_value' and resource != 'loan':
                price = market_prices.get(resource, DEFAULT_PRICES.get(resource, 0))
                calculated_value += amount * price
        
        # Subtract loan if present
        if 'loan' in resources:
            calculated_value -= resources['loan']
            
        resources['total_value'] = calculated_value
    
    return resources

def is_duplicate_entry(history_data, timestamp, total_value):
    """Check if this exact bank data has already been recorded."""
    for record in history_data["bank_records"]:
        # Check if we already have an entry with the same timestamp or same total value within a short time window
        record_time = datetime.strptime(record["timestamp"], '%Y-%m-%d %H:%M:%S')
        current_time = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
        time_diff = abs((record_time - current_time).total_seconds())
        
        # If entry exists within 5 minutes with the same value, consider it a duplicate
        if time_diff < 300 and record["resources"].get("total_value") == total_value:
            return True
    
    return False

@bot.event
async def on_message(message):
    """Event triggered when a message is sent in a channel the bot can see."""
    try:
        # Ignore messages from the bot itself
        if message.author == bot.user:
            return
        
        # Process commands (if any) from this message
        await bot.process_commands(message)
        
        # Check if a user is requesting bank information
        if message.content.strip().lower() == "$get_bank":
            print(f"Detected $get_bank command from {message.author.name}")
            
            # Updated check_tars_response function
            def check_tars_response(response):
                """Check if the response is a valid TARS response in the same channel."""
                return is_valid_tars_response(response) and response.channel == message.channel
            
            try:
                # Wait for TARS to respond (timeout after 5 seconds)
                tars_response = await bot.wait_for('message', check=check_tars_response, timeout=5.0)
                
                # Extract timestamp
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Extract resources from TARS response
                resources = await extract_resources_from_tars_response(tars_response)
                
                if resources and 'total_value' in resources:
                    # Load existing history data
                    history_data = load_bank_history()
                    
                    # Check if this is a duplicate entry
                    if not is_duplicate_entry(history_data, timestamp, resources['total_value']):
                        # Create record with metadata
                        bank_record = {
                            "timestamp": timestamp,
                            "requested_by": message.author.name,
                            "resources": resources
                        }
                        
                        # Add the new record
                        history_data["bank_records"].append(bank_record)
                        
                        # Save updated history
                        save_bank_history(history_data)
                        
                        print(f"Recorded new bank data: Total Value ${resources['total_value']:,.2f}")
                    else:
                        print(f"Skipped duplicate bank data entry")
            
            except asyncio.TimeoutError:
                print("Timeout waiting for TARS response")
            except Exception as e:
                print(f"Error processing TARS response: {e}")

    except Exception as e:
        print(f"Error in on_message: {e}")

@bot.command(name='remove_outliers')
async def remove_outliers(ctx):
    """
    Supprime les enregistrements bancaires dont la valeur totale est inférieure à 500 millions.
    """
    try:
        # Charger les données de l'historique bancaire
        history_data = load_bank_history()
        original_count = len(history_data["bank_records"])
        
        # Filtrer les enregistrements pour supprimer les outliers
        history_data["bank_records"] = [
            record for record in history_data["bank_records"]
            if "total_value" in record["resources"] and record["resources"]["total_value"] >= 500000000.0
        ]
        
        # Calculer le nombre d'enregistrements supprimés
        removed_count = original_count - len(history_data["bank_records"])
        
        # Sauvegarder les modifications
        save_bank_history(history_data)
        
        # Envoyer un message à l'utilisateur
        if removed_count > 0:
            await ctx.send(f"Suppression de {removed_count} outlier(s) dont la valeur totale était inférieure à 500 millions.")
        else:
            await ctx.send("Aucun outlier trouvé à supprimer.")
    except Exception as e:
        await ctx.send(f"Une erreur s'est produite lors de la suppression des outliers : {str(e)}")

@bot.command(name='dump_raw_tars')
async def dump_raw_tars(ctx, days: int = 1):
    """Dumps raw TARS message content to help debug extraction issues.
    
    Args:
        ctx: Discord context
        days: How many days back to scan (default: 1)
    """
    try:
        channel = ctx.channel
        cutoff_date = datetime.now() - timedelta(days=days)
        
        await ctx.send(f"🔍 Looking for TARS messages in the last {days} days...")
        
        found_tars = 0
        async for message in channel.history(limit=100, after=cutoff_date):
            if message.author.name == "TARS":
                found_tars += 1
                
                await ctx.send(f"TARS message #{found_tars}:")
                
                # Print basic message info
                message_info = (
                    f"Content: {message.content}\n"
                    f"Has embeds: {len(message.embeds) > 0}\n"
                    f"Created at: {message.created_at}"
                )
                await ctx.send(f"```{message_info}```")
                
                # Try to extract using our function
                resources = await extract_resources_from_tars_response(message)
                if resources:
                    await ctx.send(f"Extracted resources: {resources}")
                else:
                    await ctx.send("Failed to extract any resources from this message")
                
                # If it has embeds, show more details
                if message.embeds:
                    embed_info = []
                    for i, embed in enumerate(message.embeds):
                        embed_info.append(f"Embed #{i+1}:")
                        embed_info.append(f"  Title: {embed.title}")
                        embed_info.append(f"  Description: {embed.description}")
                        
                        if embed.fields:
                            embed_info.append("  Fields:")
                            for j, field in enumerate(embed.fields):
                                embed_info.append(f"    Field #{j+1}: {field.name} = {field.value}")
                    
                    # Fix: Use '\n' instead of 'newline'
                    await ctx.send(f"```{'newline'.join(embed_info)}```")
                
                await ctx.send("-----------------------------------")
                
                if found_tars >= 5:  # Limit to 5 messages to avoid spam
                    break
        
        if found_tars == 0:
            await ctx.send("No TARS messages found in the time period.")
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in dump_raw_tars: {e}")

@bot.command(name='scan_history')
async def scan_message_history(ctx, days: int = 30, channel_id: int = None):
    """
    Scan message history to find $get_bank commands and TARS responses.
    
    Args:
        ctx: Discord context
        days: How many days back to scan (default: 30)
        channel_id: Specific channel ID to scan (default: current channel)
    """
    try:
        channel = ctx.channel
        if channel_id:
            # If a specific channel ID is provided, use that channel
            channel = bot.get_channel(channel_id)
            if not channel:
                await ctx.send(f"❌ Channel with ID {channel_id} not found.")
                return
        
        # Calculate the cutoff date
        cutoff_date = datetime.now() - timedelta(days=days)
        
        await ctx.send(f"🔍 Starting to scan message history in {channel.name} for the past {days} days...")
        
        # Load existing history data
        history_data = load_bank_history()
        
        # Initialize counters
        commands_found = 0
        responses_parsed = 0
        
        # Create a progress message that we'll update
        progress_msg = await ctx.send("Scanning messages: 0 commands found, 0 responses parsed")
        
        # Start scanning the message history
        async for message in channel.history(limit=None, after=cutoff_date):
            # Skip messages from the bot itself
            if message.author == bot.user:
                continue
                
            # Check if this is a $get_bank command
            if message.content.strip().lower() == "$get_bank":
                commands_found += 1
                
                # Get messages after this command (limited search window)
                after_command = message.created_at
                before_limit = after_command + timedelta(seconds=10)  # 5 second window to find TARS response
                
                # Look for TARS response
                async for response in channel.history(limit=10, after=after_command, before=before_limit):
                    if is_valid_tars_response(response):
                        # Extract timestamp from the message
                        timestamp = message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                        
                        # Extract resources from TARS response
                        resources = await extract_resources_from_tars_response(response)
                        
                        if resources and 'total_value' in resources:
                            # Check if this is a duplicate entry
                            if not is_duplicate_entry(history_data, timestamp, resources['total_value']):
                                # Create record with metadata
                                bank_record = {
                                    "timestamp": timestamp,
                                    "requested_by": message.author.name,
                                    "resources": resources
                                }
                                
                                # Add the new record
                                history_data["bank_records"].append(bank_record)
                                responses_parsed += 1
                        
                        # Update progress every 5 found commands
                        if commands_found % 5 == 0:
                            await progress_msg.edit(content=f"Scanning messages: {commands_found} commands found, {responses_parsed} responses parsed")
                        
                        # We found the TARS response for this command, so we can break the inner loop
                        break
        
        # Sort records by timestamp to ensure chronological order
        history_data["bank_records"].sort(key=lambda x: x["timestamp"])
        
        # Save the updated history data
        save_bank_history(history_data)
        
        # Send final results
        await progress_msg.edit(content=f"✅ Scan complete! Found {commands_found} '$get_bank' commands and parsed {responses_parsed} TARS responses.")
        
        # If we found some data, show a summary
        if responses_parsed > 0:
            # Create a summary embed
            embed = discord.Embed(
                title="Historical Bank Data Scan Results",
                description=f"Successfully retrieved {responses_parsed} historical bank records",
                color=discord.Color.green()
            )
            
            # Get earliest and latest dates
            sorted_records = sorted(history_data["bank_records"], key=lambda x: x["timestamp"])
            earliest = datetime.strptime(sorted_records[0]["timestamp"], '%Y-%m-%d %H:%M:%S')
            latest = datetime.strptime(sorted_records[-1]["timestamp"], '%Y-%m-%d %H:%M:%S')
            
            embed.add_field(
                name="📅 Date Range", 
                value=f"From {earliest.strftime('%Y-%m-%d')} to {latest.strftime('%Y-%m-%d')}", 
                inline=False
            )
            
            embed.add_field(
                name="📊 Total Records", 
                value=f"{len(history_data['bank_records'])} data points available for analysis", 
                inline=False
            )
            
            embed.add_field(
                name="📈 Bank Analysis", 
                value="Use !bank_graph, !bank_history, or !bank_stats to analyze the data", 
                inline=False
            )
            
            await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred during scanning: {str(e)}")
        print(f"Error in scan_message_history: {e}")

@bot.command(name='bank_history')
async def show_bank_history(ctx, days: int = 7):
    """
    Command to display historical bank data.
    
    Args:
        ctx: Discord context
        days (int): Number of days of history to display (default: 7)
    """
    try:
        # Load bank history
        history_data = load_bank_history()
        
        if not history_data["bank_records"]:
            await ctx.send("❌ No bank history data found. Wait for users to use $get_bank.")
            return
        
        # Calculate the cutoff date
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Filter records to those within the time range
        filtered_records = [
            record for record in history_data["bank_records"]
            if record["timestamp"].split()[0] >= cutoff_date
        ]
        
        if not filtered_records:
            await ctx.send(f"❌ No bank history data found within the last {days} days.")
            return
        
        # Create a formatted message with the results
        embed = discord.Embed(
            title=f"Bank History (Last {days} days)",
            description=f"Showing {len(filtered_records)} data points",
            color=discord.Color.gold()
        )
        
        # Add the most recent data point
        newest = filtered_records[-1]
        oldest = filtered_records[0]
        
        embed.add_field(
            name="📊 Latest Bank Status", 
            value=f"Time: {newest['timestamp']}", 
            inline=False
        )
        
        # Calculate difference
        newest_value = newest["resources"]["total_value"]
        oldest_value = oldest["resources"]["total_value"]
        difference = newest_value - oldest_value
        percent_change = (difference / oldest_value) * 100 if oldest_value else 0
        
        embed.add_field(
            name="💰 Total Value", 
            value=f"${newest_value:,.2f}", 
            inline=True
        )
        
        change_emoji = "📈" if difference >= 0 else "📉"
        embed.add_field(
            name=f"{change_emoji} Change", 
            value=f"${difference:,.2f} ({percent_change:.2f}%)", 
            inline=True
        )
        
        # Add requester
        embed.add_field(
            name="👤 Requested by", 
            value=newest["requested_by"], 
            inline=True
        )
        
        # Add footer
        embed.set_footer(text=f"Use !bank_graph to see a visualization | Retrieved at {ctx.message.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in show_bank_history: {e}")

@bot.command(name='bank_graph')
async def generate_bank_graph(ctx, days: int = 30):
    """
    Command to generate and display a graph of bank value over time.
    
    Args:
        ctx: Discord context
        days (int): Number of days of data to include in the graph (default: 30)
    """
    try:
        # Load bank history
        history_data = load_bank_history()
        
        if not history_data["bank_records"]:
            await ctx.send("❌ No bank history data found. Wait for users to use $get_bank.")
            return
        
        # Calculate the cutoff date
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Filter records to those within the time range
        filtered_records = [
            record for record in history_data["bank_records"]
            if record["timestamp"].split()[0] >= cutoff_date
        ]
        
        if not filtered_records:
            await ctx.send(f"❌ No bank history data found within the last {days} days.")
            return
            
        # Extract data for plotting
        dates = [datetime.strptime(record["timestamp"], '%Y-%m-%d %H:%M:%S') for record in filtered_records]
        total_values = [record["resources"]["total_value"] for record in filtered_records]
        
        # Create a DataFrame for easier manipulation
        df = pd.DataFrame({
            'date': dates,
            'total_value': total_values
        })
        
        # Sort by date to ensure chronological order
        df = df.sort_values('date')
        
        # Create the plot
        plt.figure(figsize=(10, 6))
        plt.plot(df['date'], df['total_value'], marker='o', linestyle='-', color='blue')
        plt.title(f'Alliance Bank Value Over Time (Last {days} days)')
        plt.xlabel('Date')
        plt.ylabel('Total Value ($)')
        plt.grid(True, alpha=0.3)
        
        # Format y-axis with commas for thousands
        plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
        
        # Rotate x-axis labels for better readability
        plt.xticks(rotation=45)
        
        # Tight layout to ensure everything fits
        plt.tight_layout()
        
        # Save the plot to a temporary file
        temp_filename = 'bank_history_graph.png'
        plt.savefig(temp_filename)
        plt.close()
        
        # Send the graph image
        await ctx.send(f"📊 Bank value history graph (last {days} days):", file=discord.File(temp_filename))
        
        # Clean up the temporary file
        os.remove(temp_filename)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in generate_bank_graph: {e}")

@bot.command(name='bank_stats')
async def show_bank_stats(ctx, days: int = 30):
    """
    Command to display detailed stats about bank value changes.
    
    Args:
        ctx: Discord context
        days (int): Number of days to analyze (default: 30)
    """
    try:
        # Load bank history
        history_data = load_bank_history()
        
        if not history_data["bank_records"]:
            await ctx.send("❌ No bank history data found. Wait for users to use $get_bank.")
            return
        
        # Calculate the cutoff date
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Filter records to those within the time range
        filtered_records = [
            record for record in history_data["bank_records"]
            if record["timestamp"].split()[0] >= cutoff_date
        ]
        
        if not filtered_records:
            await ctx.send(f"❌ No bank history data found within the last {days} days.")
            return
        
        # Sort records by timestamp
        filtered_records.sort(key=lambda x: x["timestamp"])
        
        # Extract total values for stats calculation
        total_values = [record["resources"]["total_value"] for record in filtered_records]
        
        # Calculate statistics
        if len(total_values) >= 2:
            earliest_value = total_values[0]
            latest_value = total_values[-1]
            min_value = min(total_values)
            max_value = max(total_values)
            avg_value = sum(total_values) / len(total_values)
            
            # Calculate overall growth
            growth = latest_value - earliest_value
            growth_percent = (growth / earliest_value) * 100 if earliest_value else 0
            
            # Calculate daily growth rate
            start_date = datetime.strptime(filtered_records[0]["timestamp"], '%Y-%m-%d %H:%M:%S')
            end_date = datetime.strptime(filtered_records[-1]["timestamp"], '%Y-%m-%d %H:%M:%S')
            days_elapsed = max((end_date - start_date).days, 1)  # Ensure at least 1 day to avoid division by zero
            
            daily_growth = growth / days_elapsed
            daily_growth_percent = (daily_growth / earliest_value) * 100 if earliest_value else 0
            
            # Create a formatted message with the results
            embed = discord.Embed(
                title=f"Bank Statistics (Last {days} days)",
                description=f"Analysis based on {len(filtered_records)} data points",
                color=discord.Color.blue()
            )
            
            # Add fields for statistics
            embed.add_field(name="📊 Date Range", value=f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}", inline=False)
            embed.add_field(name="💰 Starting Value", value=f"${earliest_value:,.2f}", inline=True)
            embed.add_field(name="💰 Current Value", value=f"${latest_value:,.2f}", inline=True)
            
            growth_emoji = "📈" if growth >= 0 else "📉"
            embed.add_field(name=f"{growth_emoji} Total Growth", value=f"${growth:,.2f} ({growth_percent:.2f}%)", inline=False)
            
            daily_emoji = "📈" if daily_growth >= 0 else "📉"
            embed.add_field(name=f"{daily_emoji} Daily Growth", value=f"${daily_growth:,.2f} ({daily_growth_percent:.2f}%)", inline=True)
            
            embed.add_field(name="📊 Minimum Value", value=f"${min_value:,.2f}", inline=True)
            embed.add_field(name="📊 Maximum Value", value=f"${max_value:,.2f}", inline=True)
            embed.add_field(name="📊 Average Value", value=f"${avg_value:,.2f}", inline=True)
            
            # Add footer
            embed.set_footer(text=f"Use !bank_graph to see a visualization | Retrieved at {ctx.message.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"❌ Need at least 2 data points to calculate statistics. Currently have {len(filtered_records)}.")
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in show_bank_stats: {e}")

@bot.command(name='bank_resources')
async def show_resource_history(ctx, resource: str, days: int = 30):
    """
    Command to display history of a specific resource.
    
    Args:
        ctx: Discord context
        resource: Resource to track (money, food, coal, etc.)
        days (int): Number of days to analyze (default: 30)
    """
    try:
        # Validate resource name
        valid_resources = ["money", "food", "coal", "oil", "uranium", "lead", "iron", 
                          "bauxite", "gasoline", "munitions", "steel", "aluminum", "total_value"]
        
        resource = resource.lower()
        if resource not in valid_resources:
            await ctx.send(f"❌ Invalid resource name. Valid options are: {', '.join(valid_resources)}")
            return
        
        # Load bank history
        history_data = load_bank_history()
        
        if not history_data["bank_records"]:
            await ctx.send("❌ No bank history data found. Wait for users to use $get_bank.")
            return
        
        # Calculate the cutoff date
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Filter records to those within the time range and that have the resource
        filtered_records = [
            record for record in history_data["bank_records"]
            if record["timestamp"].split()[0] >= cutoff_date and
            resource in record["resources"]
        ]
        
        if not filtered_records:
            await ctx.send(f"❌ No history data found for resource '{resource}' within the last {days} days.")
            return
            
        # Extract data for plotting
        dates = [datetime.strptime(record["timestamp"], '%Y-%m-%d %H:%M:%S') for record in filtered_records]
        values = [record["resources"][resource] for record in filtered_records]
        
        # Create a DataFrame for easier manipulation
        df = pd.DataFrame({
            'date': dates,
            'value': values
        })
        
        # Sort by date to ensure chronological order
        df = df.sort_values('date')
        
        # Create the plot
        plt.figure(figsize=(10, 6))
        plt.plot(df['date'], df['value'], marker='o', linestyle='-', color='green')
        plt.title(f'Alliance {resource.capitalize()} Over Time (Last {days} days)')
        plt.xlabel('Date')
        
        # Set appropriate y-axis label based on resource
        if resource == "money" or resource == "total_value":
            plt.ylabel(f'{resource.capitalize()} ($)')
        else:
            plt.ylabel(f'{resource.capitalize()} (units)')
        
        plt.grid(True, alpha=0.3)
        
        # Format y-axis with commas for thousands
        plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
        
        # Rotate x-axis labels for better readability
        plt.xticks(rotation=45)
        
        # Tight layout to ensure everything fits
        plt.tight_layout()
        
        # Save the plot to a temporary file
        temp_filename = f'{resource}_history_graph.png'
        plt.savefig(temp_filename)
        plt.close()
        
        # Send the graph image
        await ctx.send(f"📊 {resource.capitalize()} history graph (last {days} days):", file=discord.File(temp_filename))
        
        # Clean up the temporary file
        os.remove(temp_filename)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in show_resource_history: {e}")

@bot.command(name='scan_all_channels')
async def scan_all_channels(ctx, days: int = 30):
    """
    Scan all accessible text channels in the server for $get_bank commands.
    
    Args:
        ctx: Discord context
        days: How many days back to scan (default: 30)
    """
    try:
        guild = ctx.guild
        text_channels = [channel for channel in guild.channels if isinstance(channel, discord.TextChannel)]
        
        await ctx.send(f"🔍 Starting to scan {len(text_channels)} channels for the past {days} days. This may take a while...")
        
        # Initialize counters
        total_commands = 0
        total_responses = 0
        
        # Create a progress message that we'll update
        progress_msg = await ctx.send("Starting scan...")
        
        # Load existing history data
        history_data = load_bank_history()
        
        # Scan each channel
        for i, channel in enumerate(text_channels):
            await progress_msg.edit(content=f"Scanning channel {i+1}/{len(text_channels)}: {channel.name}")
            
            try:
                # Calculate the cutoff date
                cutoff_date = datetime.now() - timedelta(days=days)
                
                channel_commands = 0
                channel_responses = 0
                
                # Scan the channel's message history
                async for message in channel.history(limit=None, after=cutoff_date):
                    # Skip messages from the bot itself
                    if message.author == bot.user:
                        continue
                        
                    # Check if this is a $get_bank command
                    if message.content.strip().lower() == "$get_bank":
                        channel_commands += 1
                        
                        # Get messages after this command (limited search window)
                        after_command = message.created_at
                        before_limit = after_command + timedelta(seconds=5)  # 5 second window to find TARS response
                        
                        # Look for TARS response - take the immediately following message
                        next_messages = [msg async for msg in channel.history(limit=2, after=after_command, before=before_limit)]
                        for response in next_messages:
                            if is_valid_tars_response(response):
                                # Extract timestamp from the message
                                timestamp = message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                                
                                # Extract resources from TARS response
                                resources = await extract_resources_from_tars_response(response)
                                
                                if resources and 'total_value' in resources:
                                    # Check if this is a duplicate entry
                                    if not is_duplicate_entry(history_data, timestamp, resources['total_value']):
                                        # Create record with metadata
                                        bank_record = {
                                            "timestamp": timestamp,
                                            "requested_by": message.author.name,
                                            "resources": resources,
                                            "channel": channel.name
                                        }
                                        
                                        # Add the new record
                                        history_data["bank_records"].append(bank_record)
                                        channel_responses += 1
                                
                                # We found the TARS response for this command, so we can break the inner loop
                                break
                
                total_commands += channel_commands
                total_responses += channel_responses
                
                # Update progress with this channel's results
                await progress_msg.edit(content=f"Scanning channel {i+1}/{len(text_channels)}: {channel.name} - Found {channel_commands} commands, {channel_responses} responses")
                
            except discord.Forbidden:
                await ctx.send(f"⚠️ No permission to read history in #{channel.name}")
            except Exception as e:
                await ctx.send(f"⚠️ Error scanning #{channel.name}: {str(e)}")
        
        # Sort records by timestamp to ensure chronological order
        history_data["bank_records"].sort(key=lambda x: x["timestamp"])
        
        # Save the updated history data
        save_bank_history(history_data)
        
        # Send final results
        await progress_msg.edit(content=f"✅ Scan complete! Found {total_commands} '$get_bank' commands and parsed {total_responses} TARS responses across all channels.")
        
        # If we found some data, show a summary
        if total_responses > 0:
            # Create a summary embed
            embed = discord.Embed(
                title="Server-wide Bank Data Scan Results",
                description=f"Successfully retrieved {total_responses} historical bank records",
                color=discord.Color.green()
            )
            
            # Get earliest and latest dates
            sorted_records = sorted(history_data["bank_records"], key=lambda x: x["timestamp"])
            earliest = datetime.strptime(sorted_records[0]["timestamp"], '%Y-%m-%d %H:%M:%S')
            latest = datetime.strptime(sorted_records[-1]["timestamp"], '%Y-%m-%d %H:%M:%S')
            
            embed.add_field(
                name="📅 Date Range", 
                value=f"From {earliest.strftime('%Y-%m-%d')} to {latest.strftime('%Y-%m-%d')}", 
                inline=False
            )
            
            embed.add_field(
                name="📊 Total Records", 
                value=f"{len(history_data['bank_records'])} data points available for analysis", 
                inline=False
            )
            
            embed.add_field(
                name="📈 Bank Analysis", 
                value="Use !bank_graph, !bank_history, or !bank_stats to analyze the data", 
                inline=False
            )
            
            await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred during scanning: {str(e)}")
        print(f"Error in scan_all_channels: {e}")

@bot.command(name='market_prices')
async def show_market_prices(ctx):
    """Display current market prices used for calculations."""
    try:
        # Get current market prices
        market_prices = await get_pnw_market_prices()
        
        # Create an embed to display prices
        embed = discord.Embed(
            title="Current Market Prices",
            description="These prices are used to calculate the total value of resources",
            color=discord.Color.gold()
        )
        
        # Add each resource price
        for resource, price in market_prices.items():
            embed.add_field(
                name=f"{resource.capitalize()}", 
                value=f"${price:,.2f}", 
                inline=True
            )
        
        # Check if we're using cached prices or defaults
        if os.path.exists(MARKET_PRICES_CACHE_FILE):
            try:
                with open(MARKET_PRICES_CACHE_FILE, 'r') as f:
                    cache_data = json.load(f)
                    cache_time = cache_data.get('timestamp', 0)
                    cache_age = time.time() - cache_time
                    
                    if cache_age < 7200:  # 2 hours
                        embed.set_footer(text=f"Using cached prices from {datetime.fromtimestamp(cache_time).strftime('%Y-%m-%d %H:%M:%S')}")
                    else:
                        embed.set_footer(text="Using fresh prices from P&W API")
            except:
                embed.set_footer(text="Using default prices (API unavailable)")
        else:
            embed.set_footer(text="Using default prices (no cache available)")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ An error occurred: {str(e)}")
        print(f"Error in show_market_prices: {e}")

@bot.command(name='collect_member_resources')
async def collect_member_resources(ctx):
    """Manually collect and record member resources data for the set alliance."""
    # Load existing history data
    history_data = load_bank_history()
    alliance_id = history_data.get("alliance_id")
    
    # Check if alliance ID is set
    if not alliance_id:
        await ctx.send("❌ Please set the alliance ID using !set_alliance_id first.")
        return
    
    # Notify user that collection is starting
    await ctx.send("⏳ Collecting member resources data, this may take a moment...")
    
    # Fetch aggregated resources data
    aggregated_data = await fetch_and_aggregate_member_resources(alliance_id)
    if not aggregated_data:
        await ctx.send("❌ Failed to fetch member resources data. Please try again later.")
        return
    
    # Create a new record with current timestamp
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    record = {
        "timestamp": timestamp,
        "resources": aggregated_data["resources"],
        "total_value": aggregated_data["total_value"]
    }
    
    # Ensure member_resources_records exists in history_data
    if "member_resources_records" not in history_data:
        history_data["member_resources_records"] = []
    
    # Add the new record and save
    history_data["member_resources_records"].append(record)
    save_bank_history(history_data)
    
    # Send confirmation with total value
    await ctx.send(f"✅ Member resources data collected: Total Value ${aggregated_data['total_value']:,.2f} at {timestamp}.")

@bot.command(name='revenue')
async def revenue_command(ctx, nation_id: int):
    """
    Commande pour afficher le revenu journalier d'une nation.
    
    Args:
        ctx: Contexte Discord
        nation_id (int): ID de la nation à analyser
    """
    try:
        await ctx.send(f"⏳ Calcul du revenu pour la nation ID {nation_id}. Cela peut prendre un moment...")
        
        # Calculer le revenu
        result = await calculate_nation_revenue(nation_id)
        
        if "error" in result:
            await ctx.send(f"❌ Erreur: {result['error']}")
            return
        
        # Créer un embed Discord pour afficher les résultats
        embed = discord.Embed(
            title=f"{result['nation_name']} (c{result['num_cities']}) - Revenu journalier",
            url=f"https://politicsandwar.com/nation/id={nation_id}",
            color=discord.Color.gold()
        )
        
        # Ajouter les informations sur la production de ressources
        for resource, data in result["monetary_resources"].items():
            embed.add_field(
                name=resource.capitalize(),
                value=f"{data['amount']:.2f} (${data['value']:.2f})",
                inline=True
            )
        
        # Ajouter la valeur monétaire totale
        embed.add_field(
            name="Valeur des ressources",
            value=f"${result['total_monetary_value']:,.2f}",
            inline=True
        )
        
        # Ajouter les informations sur les revenus monétaires
        embed.add_field(name="Revenus", value=" ", inline=False)  # Séparateur
        
        embed.add_field(
            name="Revenu brut",
            value=f"${result['income']['gross_income']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="Bonus de couleur",
            value=f"${result['income']['color_bonus']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="Revenu brut total",
            value=f"${result['income']['gross_total']:,.2f}",
            inline=True
        )
        
        # Ajouter les informations sur les dépenses
        embed.add_field(
            name="Entretien militaire",
            value=f"${result['income']['military_upkeep']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="Entretien des infrastructures",
            value=f"${result['income']['improvement_upkeep']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="Dépenses totales",
            value=f"${result['income']['gross_upkeep']:,.2f}",
            inline=True
        )
        
        # Ajouter les informations sur le revenu net
        embed.add_field(
            name="Revenu net",
            value=f"${result['income']['net_income']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="Revenu net avec ressources",
            value=f"${result['income']['monetary_net_income']:,.2f}",
            inline=True
        )
        
        # Ajouter un pied de page
        embed.set_footer(text=f"Données récupérées le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Ajouter le drapeau en thumbnail si disponible
        if result["flag"]:
            embed.set_thumbnail(url=result["flag"])
        
        await ctx.send(embed=embed)
    
    except Exception as e:
        await ctx.send(f"❌ Une erreur s'est produite: {str(e)}")
        print(f"Error in revenue_command: {e}")

@bot.command(name='alliance_revenue')
async def alliance_revenue_command(ctx, alliance_id: int):
    """
    Commande pour afficher le revenu journalier d'une alliance entière.
    
    Args:
        ctx: Contexte Discord
        alliance_id (int): ID de l'alliance à analyser
    """
    try:
        message = await ctx.send(f"⏳ Calcul du revenu pour l'alliance ID {alliance_id}. Cela va prendre un moment pour traiter tous les membres...")
        
        # Calculer le revenu
        result = await calculate_alliance_revenue(alliance_id)
        
        if "error" in result:
            await message.edit(content=f"❌ Erreur: {result['error']}")
            return
        
        # Créer un embed Discord pour afficher les résultats généraux
        embed = discord.Embed(
            title=f"{result['alliance_name']} - Revenu journalier",
            url=f"https://politicsandwar.com/alliance/id={alliance_id}",
            color=discord.Color.gold(),
            description=f"Revenus combinés de {result['processed_nations']}/{result['member_count']} membres ({result['total_cities']} villes)"
        )
        
        # Ajouter la valeur des ressources produites
        embed.add_field(
            name="📊 Valeur des ressources produites",
            value=f"${result['total_monetary_value']:,.2f} / jour",
            inline=False
        )
        
        # Ajouter les revenus et dépenses
        embed.add_field(
            name="💰 Revenus fiscaux",
            value=f"${result['income']['gross_income']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="🎨 Bonus de couleur",
            value=f"${result['income']['color_bonus']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="💵 Revenus bruts",
            value=f"${result['income']['gross_total']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="⚔️ Dépenses militaires",
            value=f"${result['income']['military_upkeep']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="🏙️ Dépenses d'infrastructure",
            value=f"${result['income']['improvement_upkeep']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="💸 Dépenses totales",
            value=f"${result['income']['gross_upkeep']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="📈 Revenus nets",
            value=f"${result['income']['net_income']:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="💎 Revenus totaux (avec ressources)",
            value=f"${result['income']['monetary_net_income']:,.2f}",
            inline=True
        )
        
        # Ajouter un pied de page
        embed.set_footer(text=f"Généré le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Temps de calcul: plusieurs minutes")
        
        await message.edit(content="✅ Calcul terminé! Voici les résultats:", embed=embed)
        
        # Si la liste des membres est trop longue pour un seul message, nous créons un message distinct
        if result['member_results']:
            # Créer un message formaté pour les 10 premiers membres par revenu
            top_members_text = "**Top 10 membres par revenu journalier:**\n\n"
            for i, member in enumerate(result['member_results'][:10], 1):
                top_members_text += f"**{i}.** {member['name']} (c{member['cities']}): ${member['total_income']:,.2f}/jour\n"
            
            await ctx.send(top_members_text)
        
        # Générer un graphique pour les principales ressources produites (optionnel)
        try:
            # Extraire les 5 ressources les plus produites en valeur
            resource_values = [(r, result['monetary_resources'][r]['value']) for r in result['monetary_resources']]
            resource_values.sort(key=lambda x: x[1], reverse=True)
            top_resources = resource_values[:5]
            
            if top_resources:
                labels = [r[0].capitalize() for r in top_resources]
                values = [r[1] for r in top_resources]
                
                plt.figure(figsize=(10, 6))
                plt.bar(labels, values, color='gold')
                plt.title(f'Top 5 des ressources produites par {result["alliance_name"]}')
                plt.ylabel('Valeur journalière ($)')
                plt.xticks(rotation=45)
                plt.tight_layout()
                
                # Sauvegarder le graphique
                chart_filename = f'alliance_{alliance_id}_resources.png'
                plt.savefig(chart_filename)
                plt.close()
                
                # Envoyer le graphique
                await ctx.send(file=discord.File(chart_filename))
                
                # Supprimer le fichier temporaire
                os.remove(chart_filename)
        except Exception as chart_error:
            print(f"Erreur lors de la génération du graphique: {chart_error}")
        
    except Exception as e:
        await ctx.send(f"❌ Une erreur s'est produite: {str(e)}")
        print(f"Error in alliance_revenue_command: {e}")

@bot.command(name='tax_history')
async def tax_history_command(ctx, alliance_id: int, days: int = 14):
    """
    Affiche l'historique des revenus fiscaux d'une alliance.
    
    Args:
        ctx: Contexte Discord
        alliance_id (int): ID de l'alliance
        days (int): Nombre de jours d'historique à analyser (max 14)
    """
    try:
        # Limiter à 14 jours maximum (limite de l'API)
        days = min(days, 14)
        
        message = await ctx.send(f"⏳ Récupération et traitement des données fiscales pour l'alliance ID {alliance_id}...")
        
        # Récupérer et traiter les données
        tax_data = await process_alliance_tax_data(alliance_id, days)
        
        if not tax_data["daily_records"]:
            await message.edit(content=f"❌ Aucun enregistrement fiscal trouvé pour {tax_data['alliance_name']} dans les {days} derniers jours.")
            return
        
        # Créer l'embed avec un résumé
        embed = discord.Embed(
            title=f"{tax_data['alliance_name']} - Historique des revenus fiscaux",
            url=f"https://politicsandwar.com/alliance/id={alliance_id}",
            color=discord.Color.gold(),
            description=f"Résumé des {len(tax_data['daily_records'])} derniers jours"
        )
        
        # Calculer les totaux sur la période
        total_money = sum(day["money"] for day in tax_data["daily_records"])
        total_value = sum(day["total_value"] for day in tax_data["daily_records"])
        total_taxes = sum(day["count"] for day in tax_data["daily_records"])
        
        # Ajouter les champs récapitulatifs
        embed.add_field(
            name="💰 Argent collecté",
            value=f"${total_money:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="💎 Valeur totale",
            value=f"${total_value:,.2f}",
            inline=True
        )
        
        embed.add_field(
            name="🧾 Transactions",
            value=f"{total_taxes:,}",
            inline=True
        )
        
        # Ajouter la moyenne quotidienne
        days_count = len(tax_data["daily_records"])
        if days_count > 0:
            embed.add_field(
                name="📊 Moyenne journalière",
                value=f"${total_value / days_count:,.2f}",
                inline=True
            )
        
        # Pied de page
        embed.set_footer(text=f"Utilisez !tax_graph pour visualiser l'évolution | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        await message.edit(content="", embed=embed)
        
    except Exception as e:
        await ctx.send(f"❌ Une erreur s'est produite: {str(e)}")
        print(f"Error in tax_history_command: {e}")

@bot.command(name='tax_graph')
async def tax_graph_command(ctx, alliance_id: int, days: int = 14):
    """
    Génère un graphique des revenus fiscaux d'une alliance au fil du temps.
    
    Args:
        ctx: Contexte Discord
        alliance_id (int): ID de l'alliance
        days (int): Nombre de jours d'historique à analyser (max 14)
    """
    try:
        # Limiter à 14 jours maximum
        days = min(days, 14)
        
        message = await ctx.send(f"⏳ Génération du graphique pour l'alliance ID {alliance_id}...")
        
        # Récupérer les données
        tax_data = await process_alliance_tax_data(alliance_id, days)
        
        if not tax_data["daily_records"]:
            await message.edit(content=f"❌ Aucun enregistrement fiscal trouvé pour {tax_data['alliance_name']} dans les {days} derniers jours.")
            return
        
        # Extraire les données pour le graphique
        dates = [record["date"] for record in tax_data["daily_records"]]
        values = [record["total_value"] for record in tax_data["daily_records"]]
        money_values = [record["money"] for record in tax_data["daily_records"]]
        
        # Créer le graphique
        plt.figure(figsize=(12, 6))
        
        # Tracer les deux lignes
        plt.plot(dates, values, marker='o', linestyle='-', color='gold', label='Valeur totale')
        plt.plot(dates, money_values, marker='s', linestyle='-', color='green', label='Argent uniquement')
        
        plt.title(f'Revenus fiscaux journaliers de {tax_data["alliance_name"]}')
        plt.xlabel('Date')
        plt.ylabel('Valeur ($)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        # Formater les étiquettes
        plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        # Sauvegarder le graphique
        temp_filename = f'tax_history_{alliance_id}.png'
        plt.savefig(temp_filename)
        plt.close()
        
        # Envoyer le graphique
        await message.edit(content=f"📊 Revenus fiscaux de {tax_data['alliance_name']} sur les {len(dates)} derniers jours")
        await ctx.send(file=discord.File(temp_filename))
        
        # Supprimer le fichier temporaire
        os.remove(temp_filename)
        
    except Exception as e:
        await ctx.send(f"❌ Une erreur s'est produite: {str(e)}")
        print(f"Error in tax_graph_command: {e}")

@bot.command(name='compare_tax_bank')
async def compare_tax_bank_command(ctx, alliance_id: int, days: int = 14):
    """
    Compare les revenus fiscaux avec la valeur de la banque de l'alliance.
    
    Args:
        ctx: Contexte Discord
        alliance_id (int): ID de l'alliance
        days (int): Nombre de jours d'historique à analyser (max 14)
    """
    try:
        # Limiter à 14 jours maximum
        days = min(days, 14)
        
        message = await ctx.send(f"⏳ Génération de la comparaison pour l'alliance ID {alliance_id}...")
        
        # Récupérer les données fiscales
        tax_data = await process_alliance_tax_data(alliance_id, days)
        
        if not tax_data["daily_records"]:
            await message.edit(content=f"❌ Aucun enregistrement fiscal trouvé pour {tax_data['alliance_name']} dans les {days} derniers jours.")
            return
        
        # Récupérer les données de banque depuis l'historique
        history_data = load_bank_history()
        
        # Filtrer les enregistrements de banque pour la même période
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        bank_records = [
            record for record in history_data.get("bank_records", [])
            if record["timestamp"].split()[0] >= cutoff_date
        ]
        
        # Organiser les données de banque par jour
        bank_by_day = {}
        for record in bank_records:
            date_str = record["timestamp"].split()[0]
            if date_str not in bank_by_day or record["resources"]["total_value"] > bank_by_day[date_str]["value"]:
                bank_by_day[date_str] = {
                    "date": date_str,
                    "value": record["resources"]["total_value"]
                }
        
        # Créer le graphique
        plt.figure(figsize=(12, 6))
        
        # Données fiscales
        tax_dates = [record["date"] for record in tax_data["daily_records"]]
        tax_values = [record["total_value"] for record in tax_data["daily_records"]]
        
        # Données bancaires
        bank_dates = [data["date"] for date_str, data in sorted(bank_by_day.items())]
        bank_values = [data["value"] for date_str, data in sorted(bank_by_day.items())]
        
        # Tracer les revenus fiscaux
        plt.plot(tax_dates, tax_values, marker='o', linestyle='-', color='gold', label='Revenus fiscaux')
        
        # Tracer la valeur de la banque si disponible
        if bank_dates:
            plt.plot(bank_dates, bank_values, marker='s', linestyle='-', color='blue', label='Valeur de la banque')
        
        plt.title(f'Comparaison revenus fiscaux vs. banque - {tax_data["alliance_name"]}')
        plt.xlabel('Date')
        plt.ylabel('Valeur ($)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        # Formater les étiquettes
        plt.gca().yaxis.set_major_formatter(plt.matplotlib.ticker.StrMethodFormatter('{x:,.0f}'))
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        # Sauvegarder le graphique
        temp_filename = f'tax_bank_comparison_{alliance_id}.png'
        plt.savefig(temp_filename)
        plt.close()
        
        # Envoyer le graphique
        comparison_text = "Revenus fiscaux vs. Valeur de la banque"
        if not bank_dates:
            comparison_text += " (aucune donnée bancaire disponible)"
        
        await message.edit(content=f"📊 {comparison_text} pour {tax_data['alliance_name']}")
        await ctx.send(file=discord.File(temp_filename))
        
        # Supprimer le fichier temporaire
        os.remove(temp_filename)
        
    except Exception as e:
        await ctx.send(f"❌ Une erreur s'est produite: {str(e)}")
        print(f"Error in compare_tax_bank_command: {e}")

# Error handlers
@show_bank_history.error
@generate_bank_graph.error
@show_bank_stats.error
@show_resource_history.error
async def command_error(ctx, error):
    """General error handler for all bank-related commands."""
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ Please provide valid arguments. Use !help_bank to see the correct usage.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Use !help_bank to see the correct usage.")
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")
        print(f"Error in command {ctx.command.name}: {error}")

@bot.command(name='help_bank')
async def show_help(ctx):
    """Display help information for bank tracking commands."""
    embed = discord.Embed(
        title="Alliance Bank and Resources Tracker Commands",
        description="Available commands for tracking alliance bank and member resources data",
        color=discord.Color.blue()
    )
    embed.add_field(name="!scan_history [days] [channel_id]", value="Scans message history for $get_bank commands", inline=False)
    embed.add_field(name="!bank_history [days]", value="Shows bank history summary (default: 7 days)", inline=False)
    embed.add_field(name="!bank_graph [days]", value="Graphs bank value over time (default: 30 days)", inline=False)
    embed.add_field(name="!set_alliance_id <alliance_id>", value="Sets alliance ID for member resources tracking", inline=False)
    embed.add_field(name="!member_resources_history [days]", value="Shows member resources history (default: 7 days)", inline=False)
    embed.add_field(name="!member_resources_graph [days]", value="Graphs member resources value (default: 30 days)", inline=False)
    embed.add_field(name="!compare_bank_members [days]", value="Compares bank and member resources values (default: 30 days)", inline=False)
    embed.set_footer(text="Bot captures $get_bank data automatically")
    await ctx.send(embed=embed)

if __name__ == "__main__":
    if not os.path.exists('.env'):
        with open('.env', 'w') as f:
            f.write("# Discord Bot Token\nDISCORD_TOKEN=your_discord_token_here\n# Politics & War API Key (optional)\nPNW_API_KEY=your_pnw_api_key_here\n")
        print("Created .env file. Please add your Discord token and optionally your P&W API key.")
    
    if not os.path.exists(BANK_HISTORY_FILE):
        with open(BANK_HISTORY_FILE, 'w') as f:
            json.dump({"alliance_id": None, "bank_records": [], "member_resources_records": []}, f)
        print(f"Created empty {BANK_HISTORY_FILE} file.")
    
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_token_here":
        bot.run(DISCORD_TOKEN)
    else:
        print("Please set your Discord token in the .env file before running the bot.")