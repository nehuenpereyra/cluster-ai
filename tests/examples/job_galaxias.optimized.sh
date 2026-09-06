#!/bin/bash
#SBATCH -J simulacion_galaxias
#SBATCH -o salida-%j.out
#SBATCH -e salida-%j.err
#SBATCH -t 12:00:00
#SBATCH -N 1
#SBATCH -n 16
#SBATCH -p medium

# Carga de módulos según manual IALP
module load python3

# Ejecución del programa de astrofísica
python3 simulacion_galaxias.py
