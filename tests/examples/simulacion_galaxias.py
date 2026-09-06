"""
Simulación de N-cuerpos astronómicos simplificada para pruebas de HPC en IALP.
"""

import time
import math

def calcular_energia(masa, velocidad):
    # Cálculo numérico simple
    return 0.5 * masa * (velocidad ** 2)

def main():
    print("Iniciando simulación de galaxias...")
    n_particulas = 5000
    datos = [i * 0.1 for i in range(n_particulas)]
    
    # Bucle nativo ineficiente con append
    resultados = []
    for i in range(n_particulas):
        energia = calcular_energia(1.0, datos[i])
        resultados.append(energia)
        
    # Escritura lenta a disco en bucle
    with open("resultados_temp.txt", "w") as f:
        for r in resultados[:100]:
            f.write(f"{r}\n")
            
    print(f"Simulación finalizada con éxito. Partículas procesadas: {n_particulas}")

if __name__ == "__main__":
    main()
