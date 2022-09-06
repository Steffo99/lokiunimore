import os
import re
import flask
import flask_sqlalchemy
import werkzeug.middleware.proxy_fix
import werkzeug.exceptions
import authlib.integrations.flask_client
import authlib.integrations.base_client

from lokiunimore import sql


app = flask.Flask(__name__)
"""
The main :mod:`flask` application object.
"""

# Get config from the environment
app.config.update(**os.environ)

rp_app = werkzeug.middleware.proxy_fix.ProxyFix(app=app, x_for=1, x_proto=1, x_host=1, x_port=0, x_prefix=0)
"""
Reverse proxied instance of :data:`.app`, to use in production with a Caddy server.
"""

db = flask_sqlalchemy.SQLAlchemy(app=app, metadata=sql.Base.metadata)
"""
:mod:`sqlalchemy` database engine, usable by the whole :data:`.app`.
"""

# Create all possible database tables
db.create_all()

oauth = authlib.integrations.flask_client.OAuth(app=app)
"""
OAuth2 :mod:`flask` extension installed on :data:`.app`.
"""

# Register the OAuth2 provider
oauth.register(
    name="google",
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    api_base_url="https://www.googleapis.com/",
    client_kwargs={
        "scope": "email profile openid",
    },
)


### Setup the app routes

@app.route("/")
def page_root():
    return flask.render_template("root.html")


@app.route("/matrix/<token>/")
def page_matrix_profile(token):
    user = db.session.query(sql.MatrixUser).filter_by(token=token).first_or_404()
    return flask.render_template("matrix.html", user=user, token=token)


@app.route("/matrix/<token>/link")
def page_matrix_link(token):
    db.session.query(sql.MatrixUser).filter_by(token=token).first_or_404()
    flask.session["matrix_token"] = token
    return oauth.google.authorize_redirect(flask.url_for("page_oauth_authorize", _external=True))


@app.route("/authorize")
def page_oauth_authorize():
    try:
        token = oauth.google.authorize_access_token()
    except werkzeug.exceptions.BadRequestKeyError:
        return flask.render_template(
            "error.html",
            when="""durante la verifica del login con Google""",
            details="""Mancano i parametri query necessari per effettuare l'autenticazione OAuth2.""",
            tip="""Rifai la procedura di connessione account da capo.<br>Se il problema persiste, inviami un'email a <a href="mailto:me@steffo.eu">me@steffo.eu</a>, e provvederò a risolvere il problema!"""
         )
    except authlib.integrations.base_client.errors.OAuthError:
        return flask.render_template(
            "error.html",
            when="""durante la verifica del login con Google""",
            details="""Qualcosa è inaspettatamente andato storto durante l'autenticazione OAuth2.""",
            tip="""Rifai la procedura di connessione account da capo.<br>Se il problema persiste, inviami un'email a <a href="mailto:me@steffo.eu">me@steffo.eu</a>, e provvederò a risolvere il problema!"""
        )

    # Not sure of why the nonce is now required or if I'm using it correctly
    google_account = oauth.google.parse_id_token(token=token, nonce=token["userinfo"]["nonce"])

    if not google_account.email_verified:
        return flask.render_template(
            "error.html",
            when="""durante la verifica del tuo account Google""",
            details="""L'email del tuo account Google non è verificata.""",
            tip="""Probabilmente hai effettuato l'accesso con l'account Google sbagliato.<br>Effettua il <a href="https://accounts.google.com/logout">logout da tutti i tuoi account Google</a> e riprova!"""
        ), 403

    if not re.match(r"(.+)@studenti[.]unimore[.]it", google_account.email):
        return flask.render_template(
            "error.html",
            when="""durante la verifica del tuo account Google""",
            details="""Questo account Google non appartiene all'organizzazione <i>studenti@UniMoRe</i>.""",
            tip="""Probabilmente hai effettuato l'accesso con l'account Google sbagliato.<br>Effettua il <a href="https://accounts.google.com/logout">logout da tutti i tuoi account Google</a> e riprova!"""
        ), 403

    local_account = db.session.merge(sql.Account(
        email=google_account.email,
        first_name=google_account.given_name,
        last_name=google_account.family_name,
    ))

    if matrix_token := flask.session.get("matrix_token"):
        matrix_user = db.session.query(sql.MatrixUser).filter_by(token=matrix_token).first_or_404()
        matrix_user.account = local_account

        db.session.commit()

        return flask.redirect(flask.url_for("page_matrix_profile", token=matrix_token))

    # TODO: Add user to matrix space