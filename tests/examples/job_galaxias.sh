#!/bin/bash
#SBATCH -J simulacion_galaxias
#SBATCH -o salida.txt
#SBATCH -e error.txt
#SBATCH -t 12:00:00
#SBATCH -N 1
#SBATCH -n 24
#SBATCH -p short

# Ejecución del programa de astrofísica
python3 simulacion_galaxias.py
