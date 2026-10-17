from django.contrib import admin

from . import models

for m in (models.Season, models.Team, models.Player, models.Match, models.Prediction, models.UserPrediction,
          models.UserBadge, models.MatchEvent, models.TeamProjection, models.TeamRating, models.BacktestResult):
    admin.site.register(m)
