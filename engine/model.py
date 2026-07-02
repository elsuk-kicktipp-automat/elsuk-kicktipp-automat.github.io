"""Dixon-Coles-Poisson-Modell für Fußball-Ergebnisse.

Erwartete Tore pro Spiel:

    log lambda_heim = mu + heimvorteil + angriff[heim] + abwehr[gast] + beta * elo_diff
    log lambda_gast = mu             + angriff[gast] + abwehr[heim] - beta * elo_diff

- angriff/abwehr je Team aus den Spieldaten (abwehr = "Anfälligkeit": höher =
  kassiert mehr), exponentiell abklingend gewichtet (Form, Dixon & Coles 1997).
- heimvorteil ligaspezifisch geschätzt; bei neutralem Platz (WM) abgeschaltet.
- beta koppelt die ELO-Differenz an das Tor-Verhältnis; wird mitgeschätzt und
  zum konfigurierten Prior regularisiert. So trägt ELO die Prognose, solange
  wenig Spieldaten vorliegen (z.B. WM-Gruppenphase), und die gefitteten
  Teamstärken übernehmen mit wachsender Datenmenge.
- rho ist die Dixon-Coles-Korrektur für torarme Ergebnisse (0:0, 1:0, 0:1, 1:1).
- L2-Regularisierung hält Teams mit wenig Daten am Ligaschnitt; ohne jegliche
  Trainingsdaten (1. WM-Spieltag) fällt das Modell auf reine Priors zurück.

Teams werden über normalisierte Namen identifiziert (siehe engine/teams.py).
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from .sources.openligadb import Match

RHO_BOUND = 0.3
ELO_SCALE = 100.0  # beta wirkt pro 100 Punkte ELO-Differenz
DEFAULT_INTERCEPT = 0.25  # exp(0.25) ~ 1.28 Tore/Team als Prior
DEFAULT_HOME_ADV = 0.25


@dataclass
class FittedParams:
    attack: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)
    intercept: float = DEFAULT_INTERCEPT
    home_adv: float = 0.0
    rho: float = 0.0
    elo_beta: float = 0.0


class DixonColes:
    def __init__(
        self,
        xi: float = 0.002,
        l2_penalty: float = 0.1,
        max_goals: int = 6,
        neutral_venue: bool = False,
        elo_beta_prior: float = 0.15,
        elo_beta_penalty: float = 50.0,
    ):
        self.xi = xi
        self.l2_penalty = l2_penalty
        self.max_goals = max_goals
        self.neutral_venue = neutral_venue
        self.elo_beta_prior = elo_beta_prior
        self.elo_beta_penalty = elo_beta_penalty
        self.params: FittedParams | None = None
        self.elo: dict[str, float] = {}

    def _elo_diff(self, home_key: str, away_key: str) -> float:
        if home_key in self.elo and away_key in self.elo:
            return (self.elo[home_key] - self.elo[away_key]) / ELO_SCALE
        return 0.0

    def fit(
        self,
        matches: list[Match],
        ref_date: datetime,
        elo: dict[str, float] | None = None,
    ) -> FittedParams:
        """Fittet auf abgeschlossene Spiele, gewichtet relativ zu ref_date.

        elo: {team_key -> rating}; None deaktiviert den ELO-Term. Ein bereits
        vorhandenes Fit-Ergebnis dient als Warmstart. Ohne Trainingsspiele
        bleiben nur die Priors aktiv (ELO + Standard-Toranzahl).
        """
        self.elo = elo or {}
        use_elo = bool(self.elo)
        matches = [m for m in matches if m.has_result]

        if not matches:
            self.params = FittedParams(
                home_adv=0.0 if self.neutral_venue else DEFAULT_HOME_ADV,
                elo_beta=self.elo_beta_prior if use_elo else 0.0,
            )
            return self.params

        team_keys = sorted({m.home_key for m in matches} | {m.away_key for m in matches})
        idx = {t: i for i, t in enumerate(team_keys)}
        n = len(team_keys)

        hi = np.array([idx[m.home_key] for m in matches])
        ai = np.array([idx[m.away_key] for m in matches])
        hg = np.array([m.home_goals for m in matches], dtype=float)
        ag = np.array([m.away_goals for m in matches], dtype=float)
        elo_diff = np.array([self._elo_diff(m.home_key, m.away_key) for m in matches])
        days_ago = np.array(
            [max(0.0, (ref_date - m.kickoff_utc).total_seconds() / 86400) for m in matches]
        )
        w = np.exp(-self.xi * days_ago)

        def log_tau(lam, mu, rho):
            tau = np.ones_like(lam)
            tau = np.where((hg == 0) & (ag == 0), 1 - lam * mu * rho, tau)
            tau = np.where((hg == 0) & (ag == 1), 1 + lam * rho, tau)
            tau = np.where((hg == 1) & (ag == 0), 1 + mu * rho, tau)
            tau = np.where((hg == 1) & (ag == 1), 1 - rho, tau)
            return np.log(np.clip(tau, 1e-10, None))

        # Parametervektor: [angriff(n), abwehr(n), mu, heimvorteil, rho, beta]
        def nll(theta):
            attack, defense = theta[:n], theta[n : 2 * n]
            intercept, home_adv, rho, beta = theta[2 * n : 2 * n + 4]
            lam = np.exp(intercept + home_adv + attack[hi] + defense[ai] + beta * elo_diff)
            mu = np.exp(intercept + attack[ai] + defense[hi] - beta * elo_diff)
            ll = w * (
                log_tau(lam, mu, rho)
                + hg * np.log(lam) - lam
                + ag * np.log(mu) - mu
            )
            penalty = self.l2_penalty * (attack @ attack + defense @ defense)
            penalty += self.elo_beta_penalty * (beta - self.elo_beta_prior) ** 2
            return -ll.sum() + penalty

        x0 = np.zeros(2 * n + 4)
        x0[2 * n] = DEFAULT_INTERCEPT
        x0[2 * n + 1] = 0.0 if self.neutral_venue else DEFAULT_HOME_ADV
        x0[2 * n + 3] = self.elo_beta_prior if use_elo else 0.0
        if self.params is not None:
            for t, i in idx.items():
                x0[i] = self.params.attack.get(t, 0.0)
                x0[n + i] = self.params.defense.get(t, 0.0)
            x0[2 * n] = self.params.intercept
            x0[2 * n + 1] = 0.0 if self.neutral_venue else self.params.home_adv
            x0[2 * n + 2] = self.params.rho
            x0[2 * n + 3] = self.params.elo_beta if use_elo else 0.0

        bounds = [(None, None)] * (2 * n + 1)
        bounds.append((0.0, 0.0) if self.neutral_venue else (None, None))  # heimvorteil
        bounds.append((-RHO_BOUND, RHO_BOUND))                             # rho
        bounds.append((None, None) if use_elo else (0.0, 0.0))             # beta

        result = minimize(nll, x0, method="L-BFGS-B", bounds=bounds)

        theta = result.x
        self.params = FittedParams(
            attack={t: theta[idx[t]] for t in team_keys},
            defense={t: theta[n + idx[t]] for t in team_keys},
            intercept=theta[2 * n],
            home_adv=theta[2 * n + 1],
            rho=theta[2 * n + 2],
            elo_beta=theta[2 * n + 3],
        )
        return self.params

    def expected_goals(self, home_key: str, away_key: str) -> tuple[float, float]:
        """(lambda_heim, lambda_gast); unbekannte Teams zählen als Ligaschnitt."""
        p = self.params
        if p is None:
            raise ValueError("Modell ist noch nicht gefittet.")
        elo_term = p.elo_beta * self._elo_diff(home_key, away_key)
        lam = np.exp(
            p.intercept + p.home_adv
            + p.attack.get(home_key, 0.0) + p.defense.get(away_key, 0.0) + elo_term
        )
        mu = np.exp(
            p.intercept
            + p.attack.get(away_key, 0.0) + p.defense.get(home_key, 0.0) - elo_term
        )
        return float(lam), float(mu)

    def score_matrix(self, home_key: str, away_key: str) -> np.ndarray:
        """Wahrscheinlichkeitsmatrix P[heimtore, gasttore] für 0..max_goals (0:0 bis 6:6)."""
        lam, mu = self.expected_goals(home_key, away_key)
        goals = np.arange(self.max_goals + 1)
        matrix = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))

        rho = self.params.rho
        matrix[0, 0] *= max(1 - lam * mu * rho, 1e-10)
        matrix[0, 1] *= max(1 + lam * rho, 1e-10)
        matrix[1, 0] *= max(1 + mu * rho, 1e-10)
        matrix[1, 1] *= max(1 - rho, 1e-10)

        return matrix / matrix.sum()
