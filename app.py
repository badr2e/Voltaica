from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_mysqldb import MySQL
import MySQLdb.cursors
import os
from dotenv import load_dotenv
import json
from datetime import timedelta


load_dotenv()

app = Flask(__name__)

app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

mysql = MySQL(app)

@app.route('/')
def home():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM charge_affaire WHERE id = %s', (session['id'],))
    user = cursor.fetchone()
    return redirect(url_for('dasboard'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Vous avez été déconnecté !', 'success')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'loggedin' in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Tous les champs sont obligatoires !', 'danger')
            return redirect(url_for('login'))

        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM charge_affaire WHERE username = %s AND motdepasse = %s', (username, password))
        user = cursor.fetchone()

        if user:
            session['loggedin'] = True
            session['id'] = user['id']
            session['nom'] = user['nom']
            session['grade'] = user['grade']
            
            return redirect(url_for('dashboard'))
        else:
            flash('Username ou mot de passe incorrect !', 'danger')

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    # Vérifier si l'utilisateur est connecté
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    # ID de l'utilisateur connecté
    
    user_id = session['id']
    
    # Récupérer l'année de sélection, sinon utiliser l'année actuelle
    annee = request.args.get('annee', default=2024)
    
    # Connexion à la base de données
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # 1. Montant des contrats payés
    cursor.execute("""
        SELECT SUM(commission) AS montant_contrats
        FROM dossier
        WHERE YEAR(datePDB) = %s 
        AND statut = 'Payé'
        AND id_chaff = %s
        """, (annee, user_id))
    result_contrats_paye = cursor.fetchone()
    montant_contrats_paye = result_contrats_paye['montant_contrats'] if result_contrats_paye else 0

    # 2. Montant des contrats prévisionnels (en cours de validation)
    cursor.execute("""
        SELECT SUM(commission) AS montant_previsionnel
        FROM dossier
        WHERE YEAR(datePDB) = %s
        AND statut = 'En attente de validation'
        AND id_chaff = %s
        """, (annee, user_id))
    result_contrats_previsionnel = cursor.fetchone()
    montant_contrats_previsionnel = result_contrats_previsionnel['montant_previsionnel'] if result_contrats_previsionnel else 0

    # 3. Montant MLM
    # On prend les dossiers des personnes encadrées par l'utilisateur
    cursor.execute("""
        SELECT SUM(d.commission) AS montant_mlm
        FROM dossier d
        JOIN charge_affaire ca ON d.id_chaff = ca.id
        WHERE YEAR(d.datePDB) = %s
        AND ca.encadrePar = %s
    """, (annee, user_id))
    result_mlm = cursor.fetchone()
    montant_mlm = result_mlm['montant_mlm'] * 0.1 if result_mlm and result_mlm['montant_mlm'] else 0

    # 4. Récupération des montants par mois (payé et prévisionnel)
    cursor.execute("""
        SELECT MONTH(datePDB) AS mois, SUM(commission) AS montant_reel
        FROM dossier
        WHERE YEAR(datePDB) = %s AND statut = 'Payé' AND id_chaff = %s
        GROUP BY mois
    """, (annee, user_id))
    revenus_reels = cursor.fetchall()

    cursor.execute("""
        SELECT MONTH(datePDB) AS mois, SUM(commission) AS montant_previsionnel
        FROM dossier
        WHERE YEAR(datePDB) = %s AND statut = 'En attente de validation' AND id_chaff = %s
        GROUP BY mois
    """, (annee, user_id))
    revenus_previsionnels = cursor.fetchall()

    mois_data = {i: {'payé': 0, 'prévisionnel': 0} for i in range(1, 13)}

    # Remplir les données payées
    for revenu in revenus_reels:
        mois_data[revenu['mois']]['payé'] = revenu['montant_reel']

    # Remplir les données prévisionnelles
    for revenu in revenus_previsionnels:
        mois_data[revenu['mois']]['prévisionnel'] = revenu['montant_previsionnel']

    # Formater les résultats pour envoyer au frontend
    result = {
        'mois': list(mois_data.keys()),
        'payé': [mois_data[mois]['payé'] for mois in mois_data],
        'prévisionnel': [mois_data[mois]['prévisionnel'] for mois in mois_data],
        'montant_contrats_paye': montant_contrats_paye or 0,
        'montant_contrats_previsionnel': montant_contrats_previsionnel or 0,
        'montant_mlm': montant_mlm or 0,
        'annee': annee
    }
    print(result)



    # Retourner la page dashboard.html avec les données dynamiques
    return render_template('dashboard.html', result=result, nom=session['nom'], grade=session['grade'], annee=annee)

@app.route('/contrat', methods=['GET', 'POST'])
def contrat():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    user_id = session['id']  # Récupérer l'utilisateur connecté

    # Initialiser les filtres par défaut
    filtre_nom_client = request.args.get('client', '')
    filtre_etat = request.args.get('etat', '')
    date_debut = request.args.get('date_debut', None)
    date_fin = request.args.get('date_fin', None)

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Construire la requête de base
    query = """
        SELECT * FROM dossier
        WHERE id_chaff = %s
    """
    params = [user_id]

    # Ajouter les filtres dynamiques
    if filtre_nom_client:
        query += " AND nom_client LIKE %s"
        params.append(f"%{filtre_nom_client}%")
    if filtre_etat:
        query += " AND statut = %s"
        params.append(filtre_etat)
    if date_debut:
        query += " AND datePDB >= %s"
        params.append(date_debut)
    if date_fin:
        query += " AND datePDB <= %s"
        params.append(date_fin)

    cursor.execute(query, params)
    contrats = cursor.fetchall()

    return render_template('contrat.html', contrats=contrats, nom=session['nom'], grade=session['grade'])

@app.route('/gestion_mlm', methods=['GET'])
def gestion_mlm():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    user_id = session['id']  # ID de l'utilisateur supervisant les chargés d'affaires

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Récupérer les données des chargés d'affaire supervisés par l'utilisateur avec commissions totales
    cursor.execute("""
        SELECT 
            ca.id, ca.nom, ca.email, ca.telephone, ca.codePostal, ca.grade, ca.photo,
            manager.nom AS nom_manager 
        FROM charge_affaire ca
        LEFT JOIN charge_affaire manager ON ca.encadrePar = manager.id
        WHERE ca.encadrePar = %s
        ORDER BY ca.nom ASC
    """, (user_id,))
    charges_affaire = cursor.fetchall()

    for charge in charges_affaire:
        # Récupérer les dossiers associés avec statut "Payé"
        cursor.execute("""
            SELECT 
                d.numero_dossier, d.commission, d.datePDB
            FROM dossier d
            WHERE d.id_chaff = %s AND d.statut = 'Payé'
        """, (charge['id'],))
        dossiers = cursor.fetchall()

        # Ajouter les dossiers à chaque chargé d'affaire
        charge['dossiers'] = []
        for dossier in dossiers:
            date_paiement = dossier['datePDB'] + timedelta(days=180)  # Date PDB + 6 mois
            charge['dossiers'].append({
                'numero_dossier': dossier['numero_dossier'],
                'montant_a_recuperer': dossier['commission'],
                'date_paiement': date_paiement.strftime('%d-%m-%Y')
            })

    return render_template(
        'gestion_mlm.html', 
        charges_affaire=charges_affaire,
        nom=session['nom'], 
        grade=session['grade']
    )



if __name__ == '__main__':
    app.run(debug=True)