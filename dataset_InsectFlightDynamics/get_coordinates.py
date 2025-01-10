import pandas as pd

def get_coordinates(data, type_name, movie_number, fly_number):
    """
    Extracts the x and y coordinates for all times for a specified type, movie number, and fly number.

    Parameters:
        data (pd.DataFrame): The dataframe containing the CSV data.
        type_name (str): The type to filter on (e.g., 'Dmelanogaster').
        movie_number (int): The movie number to filter on.
        fly_number (int): The fly number to filter on.

    Returns:
        list: A list of (x, y) tuples for all matching times, or an empty list if no match is found.
    """
    # Filter the data
    filtered = data[
        (data['Type'] == type_name) &
        (data['Movie_number'] == movie_number) &
        (data['Fly_number'] == fly_number)
    ]
    
    # Check if any rows match the criteria
    if not filtered.empty:
        return list(filtered['x']), list(filtered['y'])
    else:
        return [], []

# Example usage
if __name__ == "__main__":
    # Load the CSV file into a DataFrame
    file_path = 'Coordinate_second.csv'  # Replace with the actual file path
    df = pd.read_csv(file_path)
    
    # Extract all coordinates
    type_name = 'Dmelanogaster'
    movie_number = 1
    fly_number = 0
    x,y = extract_all_coordinates(df, type_name, movie_number, fly_number)
    
    print(f"Coordinates for {type_name}, movie {movie_number}, fly {fly_number}")
    print(x)