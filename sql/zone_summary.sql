-- cleaned_data.csv 를 places 테이블로 적재한 뒤 실행 (SQLite)

-- 1) 권역별 평균 핫플 점수와 장소 수 (장소 수가 적은 권역은 평균이 흔들리므로 함께 확인)
SELECT zone,
       COUNT(*)                 AS n_place,
       ROUND(AVG(hot_score), 1) AS avg_score,
       SUM(is_top10)            AS n_top10,
       SUM(is_fire)             AS n_rising
FROM places
GROUP BY zone
ORDER BY avg_score DESC;

-- 2) 카테고리별 평균 점수
SELECT category, COUNT(*) AS n_place, ROUND(AVG(hot_score), 1) AS avg_score
FROM places
GROUP BY category;

-- 3) 점수는 높지 않지만 최근 상승세가 뚜렷한 장소 (TOP10 밖 + 상승점수 20점 이상)
SELECT CAST(rank AS INT) AS rank, name, zone, hot_score, trend_points
FROM places
WHERE is_top10 = 0 AND is_fire = 1
ORDER BY trend_points DESC;
