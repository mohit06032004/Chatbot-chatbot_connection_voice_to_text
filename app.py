import os
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash, get_flashed_messages
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
import bcrypt
import google.generativeai as genai
import assemblyai as aai
import mistune
import re
import json

app=Flask(__name__)

# congigure the generative AI API
genai.configure(api_key=os.environ.get('GENAI_API_KEY'))
aai.settings.api_key = os.environ.get('ASSEMBLY_AI_API')

################################################### Configure envurionment variables #################################

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'session')
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = True
app.config['CORS_HEADERS'] = 'Content-Type'


CORS(app)
db=SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins='http://127.0.0.1:5000')

#  Render markdown
renderer = mistune.HTMLRenderer()
parser = mistune.create_markdown(renderer=renderer)

#########################################################  Database classes ##########################################
class User(db.Model):
    name=db.Column(db.String(80))
    email=db.Column(db.String(80), primary_key=True)
    password=db.Column(db.String(80))

    def __init__(self, email, name, password):
        self.name=name
        self.email=email
        self.password=bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password.encode('utf-8'))


    
class Session(db.Model):        
    session_id=db.Column(db.String(80), primary_key=True)
    email=db.Column(db.String(80), db.ForeignKey('user.email', ondelete='CASCADE'))

    def __init__(self, session_id, email):
        self.session_id=session_id
        self.email=email


class Message(db.Model):
    message_id=db.Column(db.String(80),primary_key=True)
    query=db.Column(db.String(5000))
    response=db.Column(db.String(5000))
    session_id=db.Column(db.String(80), db.ForeignKey('session.session_id', ondelete='CASCADE'))

    def __init__(self, message_id, query,response,session_id):
        self.message_id=message_id
        self.query=query
        self.response=response
        self.session_id=session_id
    def to_dict(self):
        return {
            'message_id': self.message_id,
            'query': self.query,
            'response': self.response,
            'session_id': self.session_id
        }


with app.app_context():
    db.create_all()



# ####################################################### Routes configuration ##########################################

# @app.route('/temp_voice')
# def temp_voice():
#     return render_template('temp_voice.html')

@app.route('/')
def index():
    if 'email' in session:
        # check if user exist in the database
        user = User.query.filter_by(email=session['email']).first()

        if user:
            return render_template('index.html', name=session['name'], email=session['email'])
        else:
            return redirect(url_for('login'))
    return redirect(url_for('login'))


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        if not is_valid_email(email):
            flash('Invalid email')
            return redirect(url_for('register'))

        if not is_valid_password(password):
            flash('Password must be at least 8 characters long')
            return redirect(url_for('register'))
        
        new_user=User(email, name, password)
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))
    
    return render_template('register.html')
    

@app.route('/login', methods=['POST', 'GET']) # Login route
def login():
    if request.method=='POST':
        email=request.form['email']
        password=request.form['password']

        user=User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            session['email']=email
            session['name']=user.name
            # set session time limit to 60 minutes
            session.permanent = True
            app.permanent_session_lifetime = 60*60

            return redirect(url_for('index'))
        else:
            flash('Invalid email or password')
            return render_template('login.html')
    else:
        return render_template('login.html')

# Chat route
@app.route('/chat')
def chat():
    try:
        email = session.get('email')
        if not email:
            flash("Please log in to access the chat.")
            return redirect(url_for('login'))

        sessions = db.session.query(Session).filter_by(email=email).all()
        session_ids = [session.session_id for session in sessions]

        messages = []
        for session_id in session_ids:
            message = db.session.query(Message).filter_by(session_id=session_id).first()
            if message:
                messages.append(message)

        messages.reverse()
        message_dicts = [message.to_dict() for message in messages]

        return render_template('chat.html', messages=message_dicts, name=session.get('name'), email=email)

    except Exception as e:
        # Log the error here
        app.logger.error(f"Error in chat route: {e}")

        # Display a user-friendly error message and redirect to an error page
        flash("An error occurred while loading the chat. Please try again later.")
        return redirect(url_for('error'))
    

@app.route('/chat/<id>')
def chat_history(id):
    try:
        messages = db.session.query(Message).filter_by(session_id=id).all()
        if not messages:
            # If no messages are found for the given ID, it's likely that the ID doesn't exist
            flash("The requested chat history does not exist.")
            return render_template('404.html'), 404

        message_dicts = [message.to_dict() for message in messages]
        return render_template('chat_history.html', messages=message_dicts, name=session['name'], email=session['email'])
    except Exception as e:
        # Log the error here
        app.logger.error(f"Error in chat history route: {e}")

        # Display a user-friendly error message and redirect to an error page
        flash("An error occurred while loading the chat history. Please try again later.")
        return redirect(url_for('error'))

   
@app.route('/clear_chats')
def clear_chats():
    try:
        if 'email' not in session:
            flash("Please log in to clear your chats.")
            return redirect(url_for('login'))

        # Delete all sessions of a user from session table
        email = session['email']

        # check if user exist in the database
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("User not found. Please log in again.")
            return redirect(url_for('login'))

        # delete all sessions of the user
        db.session.query(Session).filter_by(email=email).delete()
        db.session.commit()

        return redirect(url_for('chat'))

    except Exception as e:
        # Log the error here
        app.logger.error(f"Error in clear chats route: {e}")

        # Display a user-friendly error message and redirect to an error page
        flash("An error occurred while clearing your chats. Please try again later.")
        return redirect(url_for('error'))


@app.route('/voice')
def voice():
    return render_template('voice.html', name=session['name'], email=session['email'])

################################################### Websocket configuration ###########################################

@socketio.on('connection_id', namespace='/chat')
def handle_connection_id(connection_id):
    print(f"Received connection ID: {connection_id}")

@socketio.on('message', namespace='/chat')
def handle_message(data):
    socket_id = data['socket_id']
    message_id = data['message_id']
    message_text = data['message_text']
    email = data['email']

    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(message_text)
        html = parser(response.text)
    except Exception as e:
        # Log the error here
        app.logger.error(f"Error generating response: {e}")

        # Send an error message back to the client in HTML format with red text
        error_message = f"<p style='color: red;'>{e}</p>"
        emit('error', {'message': error_message}, room=socket_id)
        return

    # Check if the socket_id exists in the session table
    existing_sessions = Session.query.filter_by(session_id=socket_id).first()
    if existing_sessions:
        # Store the message in the message table
        message = Message(message_id=message_id, query=message_text, response=html, session_id=socket_id)
        db.session.add(message)
        db.session.commit()
    else:
        try:
            # Create a new session in the session table
            existing_sessions = Session(session_id=socket_id, email=email)
            db.session.add(existing_sessions)
            db.session.commit()

            # Store the message in the message table
            message = Message(message_id=message_id, query=message_text, response=html, session_id=socket_id)
            db.session.add(message)
            db.session.commit()
        except Exception as e:
            # Log the error here
            app.logger.error(f"Error storing message in database: {e}")

            # Send an error message back to the client in HTML format with red text
            error_message = f"<p style='color: red;'>{e}</p>"
            emit('error', {'message': error_message}, room=socket_id)
            return

    # Send the response back to the client
    emit('message', {'message_text': html}, room=socket_id)


@socketio.on('connection_voice', namespace='/voice')
def handle_voice_connect(data):
    print('Connected to voice namespace')
    
transcriber = aai.Transcriber()

@socketio.on('audioData', namespace='/voice')
def handle_audio_data(json):
    # Assuming the audio data is in json['data']
    audio_data = json['data']
    # Save the audio file to /static/audio.mp3  
    with open('static/audio.mp3', 'wb') as f:
        f.write(audio_data)
    # Transcribe the audio file
    transcript = transcriber.transcribe('static/audio.mp3')
    if transcript.status == aai.TranscriptStatus.error:
        print(transcript.error)
    else:
        # Emit the transcript text back to the client
        emit('text_data', {'text': transcript.text})

##################################################### Utility functions ##############################################
# Validate email
def is_valid_email(email):
    regex = r'^\w+([\.-]?\w+)*@\w+([\.-]?\w+)*(\.\w{2,3})+$'
    return re.search(regex, email) is not None

# Validate password
def is_valid_password(password):
    # Add your password validation rules here
    return len(password) >= 8
    

if __name__ == '__main__':
    app.run(debug=True)